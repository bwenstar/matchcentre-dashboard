# Roadmap

Internal. Lives in `notes/`, which `.gitignore` excludes, so this can be blunt
about what is unfinished and what is unwise.

The stated goal beyond 0.1.0 is one sentence: **a club should not have to run
anything by hand.** Scorecards should be ingested as they become available and the
dashboard rebuilt without a human in the loop.

That is two separate problems wearing one coat. Rebuilding without a human is
nearly free and mostly a matter of discipline we already have. *Retrieving* the
cards without a human is the hard one, and the hard part of it is not technical.

---

## 1. Rebuild without a human

### 1.1 What already makes this cheap

The design decisions taken for correctness happen to be exactly what an automated
rebuild needs. None of this is new work; it is inventory.

- **One command, no state.** `build.py` reads `data/`, writes `site/`, and keeps
  nothing between runs. There is no incremental index to corrupt and no cache to
  invalidate. A rebuild is a full rebuild.
- **The seam is a directory.** `--data` takes a path. Anything that can put PDFs
  in a directory is a valid front end, and it needs to know nothing about this
  program. Keep it that way; the moment `build.py` grows a `--fetch` flag, the
  fetcher's failure modes become the builder's.
- **Deterministic output.** Same cards in, byte-identical `season.json` out. The
  single wall-clock value is `meta.generated`, and `--generated` pins it. A
  scheduled job that passes `--generated` and diffs the result gets a diff that
  contains only real result changes. Checked by hand: the same twelve cards built
  in four separate processes with `--generated` pinned, into four directories, two
  of them under different `PYTHONHASHSEED` values, produced eight files each with
  `diff -r` clean in every pairing. That check is not yet a test (§6.2).
- **Honest exit status.** Exit 0 only when every card found was scored and
  validation raised no errors (F4). A cron job can act on that without parsing
  output. `tools/doctor.py` likewise exits nonzero per file, so a triage loop over
  a directory is three lines of shell.
- **A refusal to overwrite hand-edited output.** `site.py` names strangers in a
  competition directory and refuses rather than clobbering them. That is the right
  behaviour for an unattended job too, but note the consequence: an automated
  build will stop dead if somebody drops a file into `site/tuesday-division-c/`.
  Better to build into a scratch directory and swap.

### 1.2 What is actually missing

**A trigger.** Two candidates, and they are not equivalent.

*Scheduled.* A timer runs `build.py` every N minutes. Trivially reliable, trivial
to reason about, no new dependency (`cron`, `systemd --user`, Task Scheduler). The
cost is latency and pointless work — most runs find nothing new. At ~15 ms per
card measured on a twelve-card season, a full rebuild of a thousand-card archive
is about fifteen seconds, so pointless work is cheap enough to ignore for a long
time.

*Watched directory.* React when a file appears. Lower latency, and considerably
more ways to be wrong: a PDF is visible on disk before it is completely written,
so any watcher needs a settle delay or a size-stability check, and a bulk copy of
forty cards will fire forty events that must coalesce into one build. The standard
library has no file-system watcher — a polling loop over `os.scandir` with mtime
and size comparison is the only stdlib-only option, which is a scheduled build
with extra steps.

**Recommendation: scheduled, with a cheap change detector.** Hash the sorted list
of `(relative path, size, mtime_ns)` under `--data`, compare against the previous
run's, and skip the build when it is unchanged. That is a handful of lines, keeps
the no-state property (the hash is a cache, and a lost cache costs one extra
rebuild), and makes the timer interval a free parameter. Do *not* let the detector
decide which cards to parse — see §3.

**Somewhere to put the log.** Right now the report goes to stdout, which is
correct for a human and useless at 4am. An unattended run needs the report kept
per-run, and needs the *previous* run's report kept too, because "this build
started failing" is only visible as a comparison. Plain files in a directory,
newest last; no log rotation cleverness.

**A way to be told.** An unattended failure that nobody reads is the same as no
validation at all — the exact failure mode this project exists to prevent, moved
up a layer. Minimum viable: the wrapper writes a one-line status file that the
dashboard's front page displays ("last build: 2026-09-10 04:00, 3 cards refused").
Putting the failure on the page the club already looks at beats email, which needs
credentials and a server and gets filtered.

---

## 2. Where the PDFs come from

This is the part that does not have a clean answer, and it is worth writing down
why before anyone spends a weekend on it.

### 2.1 The permissions question comes first

Match Centre requires a login, and the scorecard export is commonly available to
team captains and club administrators rather than to the public. So automating
retrieval means one of:

- a stored credential belonging to a real person, replayed by a script;
- a session cookie harvested from a browser and reused;
- a documented, sanctioned interface, if one exists.

The first two are the ones that are easy to build, and they are the ones that
create the problems:

- **They are somebody's identity.** A script holding a club administrator's
  password can do everything that administrator can do, including enter results.
  That is a much larger blast radius than "download some PDFs", and it now lives
  in a config file on a club laptop.
- **They break silently and confusingly.** A password change, a session expiry or
  an added second factor turns the fetcher into a thing that downloads a login
  page and saves it as `card.pdf`. Which, note, this tool already handles
  correctly: it refuses the file and names it (F4, F5). That is reassuring but it
  is not a substitute for the fetcher noticing.
- **They may not be permitted.** Automated access to Tennis Australia's site is
  governed by its terms of use, and by whatever agreement the association has with
  its clubs. This cannot be settled by reading code. It is a question for the
  association and, plausibly, for Tennis Australia.

**So: before any fetcher is written, ask.** Ask the association whether an
official export, feed or bulk download exists — associations often have one that
individual clubs are never told about. Ask whether automated retrieval with a club
account is acceptable, and get the answer in writing. A sanctioned once-a-week
bulk export that has to be triggered by hand is worth more than an unsanctioned
scraper that works until it doesn't.

If the answer is no, that is a real answer, and the fallback is good: keep the
manual export and automate everything downstream of the directory. The club's
weekly job becomes "save six PDFs into a folder", which is not nothing but is also
not much.

### 2.2 If retrieval is sanctioned, what it looks like

Notes toward it, kept because they were gathered from real cards and are not
obvious:

- The cards are rendered server-side from an HTML template (the producer string on
  real exports is `wkhtmltopdf 0.12.6 / Qt 5.15.13`). Every card from the site has
  the same structure and only the letterhead differs, which is why one reader
  works for every club.
- Cards carry a match id in the header identity line, and on the real cards
  examined those ids were **sequential within a round**. That suggests an
  id-addressable endpoint, which would make retrieval enumeration rather than
  scraping. It also means an off-by-one enumerates somebody else's club's matches,
  so an enumerating fetcher must verify the letterhead on what it downloaded and
  discard anything that is not this club's.
- The handover format should stay **a plain tree of PDFs**, not a database dump or
  an archive. It survives any transfer mechanism, it is reviewable by eye, and it
  keeps the fetcher and the builder independently testable.
- The fetcher should **not** sort by round or division. Routing comes from each
  card's own contents, so one flat directory of everything retrieved is the
  correct output. Sorting it would be a second, worse implementation of
  `discover.route`.
- The fetcher must assert **how many** cards it expected, per round, and say so
  when it got fewer. This is the one check the builder structurally cannot make:
  with a derived team list, a missing card and a resting team are identical from
  the inside (F1), and that is precisely the ambiguity a fetcher can resolve
  because it knows the draw it was iterating.
- Never overwrite a card in `data/` with a re-fetch of the same match without
  noticing the content changed. A corrected result is a real event and the club
  wants to know. Compare bytes, keep both, let F9 report the duplicate.

### 2.3 The airgap case

The machine that can reach the club's site may not be the machine that does the
processing — this project was developed under exactly that constraint. The design
already accommodates it, because the seam is a directory:

```
[networked machine]                 [processing machine]
  fetch scorecard PDFs                python3 build.py   (unchanged)
        |                                    ^
        +---- a plain directory of PDFs -----+
```

Nothing in the pipeline needs to know which side of the gap it is on. Keep it that
way.

---

## 3. Incremental parsing

Tempting, and currently unnecessary.

Measured this session: 12 synthetic cards parse in 178 ms, about 15 ms per card.
Linear in card count, so one club's ten-year archive of ~2,000 cards is roughly 30
seconds of parsing. A club that rebuilds nightly will not notice. A multi-club
service would.

If it is ever needed, the shape that does not break the guarantees is a **parse
cache keyed by content hash**: `sha256(file bytes) -> serialised Card`. Content
hash, not mtime, because mtime changes on a copy and does not change on a
same-size in-place edit. The cache must be treated as an accelerator with no
authority:

- a cache miss parses the file, as now;
- a cache hit must still go through every downstream check — grouping, validation,
  cross-checks — because those depend on the *set* of cards, not on one card;
- deleting the cache must change nothing but the runtime, and there should be a
  test that asserts exactly that: build twice, once cold and once warm, assert the
  documents are byte-identical.

What must not happen is a cache that lets a card skip validation, or an
"incremental" mode that adds a round to an existing `season.json`. Ladder
positions, points averages, `ladder_complete` (F10) and the bye logic (F1) are all
functions of the whole season. Recomputing them from scratch every time is not
inefficiency, it is the reason the numbers are trustworthy.

---

## 4. Publishing the built site

Today: `site/` is written locally, `.gitignore` excludes it, and a human opens
`site/index.html`. For the club to *read* the dashboard without a human step, it
has to be somewhere they can reach.

Options, roughly in order of how much they cost the club:

1. **A shared folder.** Build into a synced or network directory. No new
   technology, no hosting, no public exposure. Works with the "zip a competition
   folder and email it" property the site already has. Weakest failure mode: a
   half-written build being read. Fix by building to a scratch directory and
   renaming into place.
2. **Static hosting from the repository.** The site is entirely static and works
   over `file://`, so it works over HTTP trivially. This needs `.gitignore` to
   stop excluding `/site/` — the comment there already says so — and it needs
   `--generated` pinned, or every publish is a diff of one timestamp. Deliberately
   no continuous-integration configuration ships with this project, and adding one
   is a decision, not a detail.
3. **A small web server on a club machine.** More moving parts than the dashboard
   itself. Hard to justify.

**Whatever the mechanism: publishing is a privacy decision, not a deployment
one.** The dashboard contains full names, ratings and, in the match detail, who
played with whom every week. A club may be entirely happy with that on a members-
only page and not at all happy with it indexed by a search engine. Before any
publishing step is automated, the tool should be able to answer "what personal
information is in this output" — which argues for a small, honest feature: a build
option that renders players as initials or as roster positions. That is a
genuinely new capability, not a config label, and it needs its own thinking about
how per-player pages work when players have no names.

---

## 5. Growth: more clubs, more seasons

Multi-season is implemented (`seasons = "multi"`, season key from the first path
segment, ids carrying the season, per-season ladders, cross-season career totals).
The growth risks are elsewhere.

**Many seasons.** Rebuild cost is linear and the front page groups by season, so
the mechanical part is fine. What degrades is human review: the review checklist
on a first run over ten seasons of cards is unreadable, and `config.toml` becomes
enormous because every competition gets a section. Worth considering: per-season
config files, or a `--only <season>` filter so a club can rebuild the current
season without re-reviewing the archive. Neither is urgent and both add surface.

**Multiple clubs.** Nothing in the engine knows a club's name; `site.py` takes it
from the config and the theme key is derived per club. So running one checkout per
club works today and is the honest answer. A single installation serving several
clubs would need: a config that holds several clubs, a `data/` layout that
distinguishes them (and the letterhead on each card is real evidence for that,
unlike a folder name), and an output tree with a club level above the season
level. Do not build this speculatively. One checkout per club costs nothing.

**Format and rules drift.** More clubs means more set rules and more match shapes.
The presets are data and adding one is a dictionary entry, which is the right
shape. The thing to watch is that `min_sets_for_result` still cannot be detected
and defaults to three quarters of the slots; a club whose competition disagrees has
to know to set it, and nothing currently prompts them.

---

## 6. What must be verified before any of this ships

In roughly this order. Nothing here is done yet unless stated.

1. **The equivalence test, by hand, once.** Run the finished tool over the
   predecessor's real `data/` and assert the ladder, points, per-team and
   per-player numbers match the known-good published output. This cannot be
   committed — the fixtures are real people — so it is done manually and recorded
   in `notes/VERIFICATION.md`. Until it is, "correct" means "self-consistent".
2. **Byte-identical rebuild, as a test.** Done by hand and it held: the same twelve
   cards, `--generated` pinned, four separate `build.py` processes into four
   directories, two of them under deliberately different `PYTHONHASHSEED` values
   (1 and 99991) because set and dictionary iteration order is the likeliest source
   of a spurious difference. `diff -r` was clean across all eight files in every
   pairing. What is missing is only that it is not committed as a test, and that it
   has been run on one machine and one Python build.
3. **The anti-fixtures actually refuse.** Cards written with a page offset, a
   missing column rule, an extra tick, a dropped `ToUnicode` — each asserted to be
   *refused* (F5, F6), not silently scored. This is the test that guards against
   generator and reader agreeing with each other because they share constants.
4. **A cold-start run on a machine that has never seen the project.** Fresh clone,
   stock Python 3.11, empty `data/`, no config. It must produce a comprehensible
   message rather than a traceback. Then one real card, then a season.
5. **A run against a card from a different association.** Different letterhead,
   different division wording (`Grade`, `Section`), no ball colour, no match id,
   possibly a different number of set rows. The reader is written to tolerate all
   of that; none of it has been confirmed against a real foreign card, and until it
   is, "works for any club" is a design intention.
6. **Before automating retrieval:** the written answer from the association about
   whether it is permitted, and confirmation of whether a sanctioned export exists.
   This gates §2 entirely and is not a coding task.
7. **Before automating publishing:** a decision, recorded, about whether player
   names go on a public page — and if not, the anonymised-output feature exists
   and has a test.

---

## 7. Deliberately not planned

Kept here so it does not get re-proposed.

- **Reading results from anything but a scorecard.** No HTML scraping of ladders,
  no spreadsheet import, no manual score entry. The moment a result can enter the
  system from a second source, the guarantee that a score came from a card is
  gone, and that guarantee is the product.
- **A database.** The inputs are a directory of PDFs and the output is a directory
  of static files. Both are inspectable by eye, which is worth more here than any
  query capability.
- **A dependency, for anything.** Including for the PDF reading, the watcher, the
  templating and the tests. The no-install property is why this runs on a locked-
  down club laptop at all.
- **Guessing anything currently reported as derived.** Every item on the review
  checklist is there because guessing it was measured producing a confidently
  wrong dashboard. Convenience does not outrank that.
