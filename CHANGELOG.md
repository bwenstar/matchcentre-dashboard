# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-14

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
- Provenance on every derived identity — team names, player names, captains,
  season labels, draw and season length each recorded as declared or derived, and
  rendered on the dashboard with a visible marker.
- Ladder state stated rather than implied: the ladder is labelled as in progress
  whenever teams have played unequal numbers of matches.
- Multi-season mode: `seasons = "multi"` files cards by the first folder level
  under `data/`, writes to `site/<season>/<competition>/`, adds a season picker,
  and adds cross-season player career totals. Seasons are never merged into one
  ladder.
- `season.json` beside each dashboard: the same ladder, team, player, match,
  head-to-head and validation data as plain JSON.
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
