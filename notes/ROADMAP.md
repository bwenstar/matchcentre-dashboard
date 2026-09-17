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
segment, ids carrying the season, per-season ladders). Cross-season career totals
are *not*: this line claimed them, three shipped files repeated the claim, and no
such code exists. The docs now say so; the feature is listed in §8 below as a real
piece of unbuilt work rather than a described one. The growth risks are elsewhere.

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

---

## 8. Suggested next work, in order

Written after the first run against a club's real cards and its association's real
points sheet (`notes/VERIFICATION.md` §9). Ordered by value per hour, not by size.

1. ~~**Two wrong words, both found on real data.**~~ **Done.** `set-tied` no longer
   claims a rubber with no games on it finished level: the 0-0 case is split out as
   `set-no-games`, which says nobody is credited the set and that a rubber never
   played reads like this when the card does not say so. And `web/app.js` now labels
   `fill_in_appearances` as matches, with a comment naming `stats.py` as the reason,
   because `fill_ins` holds match keys.

2. **Reconcile against the association's published sheet, as a build step.** An
   optional `[competitions.<id>.expected]` table holding what the sheet says —
   points and matches played per team — with the build reporting any team whose
   computed figure differs, and by how much. This is exactly the check done by
   hand for §9, it is the check a club will do every week regardless, and it is
   the only check that catches a wrong `unplayed`, a wrong `sets` rule or a
   missing card in one line. Note where the line is: this reads an *expectation*
   and reports disagreement. It must never read a *result* — §7 stands — so a
   mismatch is a warning naming both figures and nothing on any page moves.
   Against the real data it would have named the one residual 0.2 gap (a card
   whose own `TOTAL`, tick and set rows all agree on 2.9 where the sheet implies
   3.1) instead of leaving it to be found by eye.

3. **The unattended trio, from §1.2.** A change detector over `(path, size,
   mtime_ns)`, per-run report files kept newest-last, and a one-line build status
   on the front page. All three are stdlib, all three are testable here, and
   together they deliver the half of the goal that needs no network: nobody has to
   run the script.

4. **The byte-identical rebuild, as a committed test** (§6.2). Held by hand across
   four processes and two `PYTHONHASHSEED` values; uncommitted, it guards nothing.
   It is what makes an automated nightly diff mean "a result changed".

5. **Anonymised output** (§4). A build option rendering players as initials or
   roster positions. Needed *before* anything is published, not after, and it is
   real work: per-player pages have to still make sense when players have no
   names.

6. **A nudge toward `min_sets_for_result`.** It cannot be detected, defaults to
   three quarters of the slots, and nothing prompts a club whose grade disagrees.
   The evidence exists at build time: when a competition has a match that was
   played but not decided, say so on the review checklist and name the setting.
   Written commented into a generated config (`config.py:704`) is not the same as
   raised at the moment it matters.

7. **Cross-season career totals.** Genuinely unbuilt, and now documented as such.
   Worth doing only with the traceability rule intact: a career figure must be
   presented as a sum of per-season figures a reader can still open and check,
   never as a number with no season behind it.

8. **`--only <season>`** (§5), so a club rebuilding this week's cards does not have
   to re-read a review checklist covering ten years of archive.

### 8.1 What can be built for the fetcher without a network

The fetcher itself is gated on §2.1 and stays gated. What is *not* gated is its
contract, and the contract is testable on loopback:

- Serve a directory of `tools/demo.py` cards over `http.server` on 127.0.0.1 and
  write the fetcher against that. Stdlib, no network, and it exercises retrieval,
  naming, the not-a-PDF path, the duplicate path and the expected-count assertion
  — everything except the real site's authentication and HTML.
- Keep it `tools/fetch.py`, invoked separately, writing only into a directory.
  `build.py` must not learn to fetch (§1.1).
- The three assertions that belong to the fetcher because the builder structurally
  cannot make them: verify the letterhead of every file before keeping it (ids are
  sequential within a round, so an off-by-one retrieves another club's match);
  never overwrite a card without comparing bytes first, because a corrected result
  is a real event; and assert the expected card count per round, since from inside
  a season a missing card and a resting team are identical (F1).
- The classic failure — a login page saved as `card.pdf` — is already handled
  correctly downstream: it is refused by path with a reason (F4, F5). That is a
  safety net, not a substitute for the fetcher noticing.

---

## 9. The GitHub-readiness pass, and what it deliberately left

Done in the run recorded as `notes/VERIFICATION.md` §10. Listed here because half of
it changes what the *next* work should be.

**Delivered.** `tools/doctor.py --redact`; the version stamped on all five generated
artefacts from one constant; `tests/test_hostile_input.py` and the `DiscoveryError`
bug it found; `tests/test_doctor.py`; `tools/screenshots.py` and the five README
images; `.github/workflows/tests.yml` (3.11, 3.12, 3.13, no install step) and
`demo.yml` (the demo season published to Pages); two issue forms, a pull-request
template and `CONTRIBUTING.md`; a README quick start.

Two of those change the shape of a support conversation, which is worth saying
plainly. A club can now report a card that will not read *without sending the card*,
and the wrong-number issue form asks which scoring scheme is in force before it asks
anything else — because a ladder computed under the wrong scheme is not an error, it
is a plausible, wrong table, which is worse and is the failure this project is most
likely to be blamed for.

**Deferred on purpose, in the order they are worth doing.**

1. **The `[competitions.<id>.expected]` reconciliation table** — still §8 item 2,
   and now the most valuable thing on the list. Unchanged by this pass, and the
   reason it was deferred is only sequencing: it is a scoring-adjacent feature and
   this pass was deliberately confined to packaging, so that nothing in it could
   move a number on a page.

2. **A reader registry, for other associations' scorecard layouts.** The idea is one
   entry point that tries each known layout in turn and reports which one matched, so
   that a club whose association prints a different letterhead is a *registration*
   rather than a fork. Deferred because it cannot be designed yet, and the missing
   input is a real foreign card (§6 item 5): a registry built from one layout and
   imagination would encode this template's assumptions in the shape of the plugin
   interface, which is the expensive kind of wrong. The honest order is: obtain a
   card from another association, make it read, and only then factor out what the two
   readers turned out not to share.

   Note the line this must not cross. §7 rules out reading results from anything but
   a scorecard, and that stands: a registry is about *more layouts of scorecard*, not
   about a second kind of source. An HTML ladder, a spreadsheet or a typed-in score
   is still refused, for the reason given there.

3. **Anything for the network fetcher.** Still gated on §2.1, which is a permissions
   question and not a coding one.
