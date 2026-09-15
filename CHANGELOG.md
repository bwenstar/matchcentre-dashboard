# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-15

Initial release.

### Added

- `build.py`: the single command. Reads every PDF under `data/`, groups the cards
  into competitions, scores them, and writes one self-contained dashboard per
  competition into `site/` plus a front page listing them all. Options:
  `--data`, `--out`, `--config`, `--check`, `--discover`, `--force`, `--quiet`,
  `--generated`.
- Scorecard reader for the PDFs exported from
  <https://matchcentre.tennis.com.au>. Cards are read by measuring the page's own
  printed table — the vertical rules that form the columns, the bands they span,
  and the hairline squares that form the tick boxes — rather than by assuming
  where anything sits. A page whose structure cannot be measured is refused by
  path with a reason, and never scored from fallback positions.
- Cross-checking of every card against the other places it states its own result:
  the printed `TOTAL` row, the result verb between the team names, and the
  `Match Winner` tick. Agreement is required before the numbers are trusted; a
  disagreement is an error naming both readings.
- A card whose banner reads `VS` reported as a card holding no result, not as a
  card this program failed to understand. Match Centre prints `VS` between the team
  names for a fixture it has no result for, and 3 of 22 real exported cards did;
  reported as an unrecognised verb, that reads as *this card was not counted*, which
  is the one thing it does not mean. Such a card is read, scored and counted like
  any other — the set rows are what the tool scores from — and what the report says
  it lost is the one cross-check the banner would have provided. A word that
  genuinely is not in the vocabulary is still reported as one, separately, and a
  match nobody played is reported once as `no-result` rather than twice.
- Routing from card contents only. A card's competition comes from the division
  printed on it and the weekday of its own date. Nothing is read from a file name
  or a folder name, with one opt-in exception: in multi-season mode the first
  folder level under `data/` is the season.
- Teams and players identified by name-derived slug rather than by draw number, so
  a late card, an added team or a different processing order does not shuffle
  anything. Draw numbers are display metadata only.
- Match format read off each card, from the marker column beside each set row, so
  the number of sets and which of them are doubles are data rather than
  assumptions. Named presets `doubles-2`, `doubles-3`, `doubles-4`,
  `doubles-singles-doubles`, `mixed-3`, `mixed-5`, `singles-3`, `singles-4`,
  `singles-6`, `singles-doubles`, or an explicit slot list. A declared format that
  disagrees with a card stops the run rather than overriding it.
- Set completion governed by a named set rule rather than a fixed games count:
  `first-to-6`, `advantage-6`, `six-game-tiebreak`, `fast4`, `short-sets-4`,
  `pro-8`, `pro-8-tiebreak`, `pro-10`.
- Scoring presets `games-bonus` (the default), `sets-first`, `rubbers` and
  `simple`, each carrying its own points values, tiebreak order, forfeit handling
  and ladder ordering. The active scheme, set rule and match format are printed on
  every run and shown on the dashboard.
- Forfeit value computed from the match format rather than held as a constant, so
  a clean-sweep forfeit is priced correctly under any format.
- What a round a team did not play is worth as a setting of its own — `unplayed`
  under `[rules]`, overridable per competition. `average` credits a bye or a
  washout the team's own average over the matches it did play and counts it as a
  match played; `zero` scores it as nothing and does not count it. `zero` is the
  default under every preset, because it is the convention that needs no
  explaining — a round nobody played adds nothing — and averaging is the surprising
  one, putting points on the board for a night off. It is only a default: two
  associations running the same points scheme disagree on this line routinely, and
  getting it wrong changes the points column of every team that has had a week off.
  It cannot change the ladder's order — crediting a team its own average cannot move
  that average, and the average is what orders the ladder — so what it changes is
  exactly the total a club checks against its association's published sheet.
  Printed on every run and shown on the dashboard, in one sentence generated from
  one function so the terminal and the page cannot say different things. A misspelt
  value stops the run rather than falling back to the preset, because a club that
  sets this line has already noticed the difference.
- A match that was not played contributes nothing anywhere, including when part of
  its card was filled in first. A washed-out night regularly stops partway: two
  rubbers finish, the rain arrives, and Match Centre keeps what was entered, so the
  card arrives with real games on it and a ticked `Match Cancelled` box. Those games
  are not a partial result to be counted at reduced strength — they are counted
  nowhere. Not in a team's games or sets, not in its home-and-away split, not in the
  per-slot table, not as a match played or a match available, and not as an
  appearance on any player's record. One predicate decides it, `Match.counts_as_played`,
  consulted at every aggregation site rather than restated at each; the browser
  mirrors it once, in `countsAsPlayed`, so the tiles a page computes for itself
  cannot disagree with the ladder printed beside them. A match played to the end with
  one rubber left unfinished is the opposite case and is unaffected: it happened, so
  its games count and it takes a ladder result if enough rubbers finished.
- Match Centre's `Unspecified player` placeholder read as *no name recorded* rather
  than as a person. It appears where a name was never entered, and taken literally it
  becomes a player named "Unspecified Player" with a playing record, a team and a
  page of their own. Such cells produce no player, in the set rows and in the roster
  block alike; the rubber's games still count if the match was played. Dropping them
  silently would be the other failure — indistinguishable from a card that never had
  the row — so they are counted per row and reported once per card as
  `players-unrecorded`, saying how many cells across how many rubbers. The message
  distinguishes the two real cases: a card called off before the names went in, where
  there is nothing to do, and a card that was played whose rubbers have games but
  nobody to credit them to, which wants fixing in Match Centre and exporting again.
- Configuration in `config.toml`, read with `tomllib`. A run that finds no config
  writes one, filled in from what the cards revealed, with every worked-out value
  written commented and annotated with what confirming it would change — and
  builds the dashboard anyway, so nothing has to be configured before the tool
  can be seen working. `config.example.toml` documents every setting.
- Rules against recording absence of evidence as evidence: no bye inferred
  without a confirmed team list; no unplayed fixture invented from a draw read off
  cards; no season length assumed from rounds played; every PDF found either
  scored or reported by path; duplicate cards reported with both paths rather than
  one overwriting the other; a card naming a team outside a confirmed team list
  treated as an error, and as a warning with a near-miss suggestion when the list
  is not confirmed; finals rounds excluded from ladder points.
- Byes stated by team instead of by fixture: a `[competitions.<id>.byes]` table,
  one line per team listing the rounds it sits out, which is the shape an
  association's bye column already has. Round labels are written as the cards print
  them, so `[1, 6]` and `["SF"]` both work, and one round needs no brackets. No
  draw, no fixtures and no dates are required to fill it in. A statement wins over
  inference, and only in the round it is made, so the weeks the cards cannot show —
  a round nobody played, a round more than one team is missing from — can be entered
  while every other round is still worked out from the cards as before. A stated
  round with no cards at all becomes a round of the season with everybody resting
  in it. A team named there that a confirmed team list does not have stops the run,
  since a bye is the one thing that pays a team for playing nothing and a misspelling
  would pay a team that does not exist. A first run writes back every bye it inferred,
  commented out, so a club checks them against the sheet rather than transcribing
  them; and declaring one round of the draw by hand no longer discards the byes
  inferred in the rounds around it.
- Provenance on every derived identity — team names, player names, captains,
  season labels, draw and season length each recorded as declared or derived, and
  rendered on the dashboard with a visible marker.
- Ladder state stated rather than implied: the ladder is labelled as in progress
  whenever teams have played unequal numbers of matches.
- Half-up rounding on every figure printed, in the Python and in the browser alike,
  so a column can be compared against a published ladder digit for digit. Exact
  halves are the point: `6.225` is held as 6.22499999999999964 by any binary float,
  so `round()` in Python and `toFixed()` in JavaScript both produce `6.22` where a
  spreadsheet produces `6.23`. The Python rounds through `Decimal(str(value))` and
  the page through the shortest decimal string that round-trips the double, so the
  two agree by construction rather than by coincidence, and every number the page
  prints goes through the one function.
- Multi-season mode: `seasons = "multi"` files cards by the first folder level
  under `data/`, writes to `site/<season>/<competition>/`, and adds a season picker
  grouped by season. Nothing is totalled across seasons — not a ladder, and not a
  player's record — so every figure on a page is accounted for by that season's own
  cards.
- `season.json` beside each dashboard: the same ladder, team, player, match,
  head-to-head and validation data as plain JSON.
- Every link in the generated site names the page it opens rather than the folder
  holding it, so the dashboard navigates with no web server: a link ending at a
  directory relies on a server to resolve it, and a browser opening the files
  directly shows a folder listing instead. The link back to the front page is
  aimed when the site is written, from the nesting the site itself uses, so it is
  correct in a multi-season tree before any script runs.
- Deterministic output. The same cards produce byte-identical files; the only
  wall-clock value is the generated timestamp, which `--generated` pins.
- `tools/doctor.py`: explains what a PDF is and why it did or did not read as a
  scorecard — metadata, font encodings, rectangle census, column rules, row bands,
  tick-box census, header fields, and a plain-language verdict. Exits nonzero when
  a page does not read, so it can be looped over a folder.
- `tools/demo.py`: writes a fictional club's season of scorecard PDFs, so the tool
  can be tried end to end without any real data. Deterministic for a given seed.
- `README.md`, `data/README.md` and `config.example.toml` for clubs;
  `CHANGELOG.md`; MIT `LICENSE`.
- `data/` and `config.toml` both kept out of git by default, with a one-line opt-out
  each. The cards name real players; a filled-in `config.toml` names real captains,
  and its `[player_names]` and `[aliases]` tables exist precisely to hold real
  people's names — so a fork or a pull request would publish a club's membership
  either way. `config.example.toml`, in which every name is invented, is committed in
  its place, and `data/README.md` survives the ignore rule so a fresh clone still
  explains what the empty folder is for.
- Test suite in `tests/`, standard-library `unittest`. Fixtures are generated
  in-process from fictional clubs and fictional players; no PDF is committed, and
  no real person's name appears anywhere in the project. The generated cards are a
  structural replica of a real exported scorecard — the same two hundred
  rectangles, shape for shape and count for count, each within a twentieth of a
  point — so the reader is exercised against the page it meets in the field rather
  than a simplified one. The suite also runs the documented commands and asserts
  that every sample output quoted in `README.md` — the build report, the ladder and
  the card checker's verdict — is what they actually print, and that the README's
  own cross-references land on headings that exist.

### Requirements

- Python 3.11 or newer, for `tomllib`. The standard library only: no `pip
  install`, no build step, no server, no network access at run time. `build.py`
  checks the version on startup and explains the problem if it is too old.
