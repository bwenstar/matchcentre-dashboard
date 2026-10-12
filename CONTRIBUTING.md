# Contributing

Thank you. This is a small project with two unusual constraints, and knowing them
first will save you a round trip.

## The two rules

**1. No dependencies. Ever.** The whole thing is the Python standard library —
including the PDF reader, the ladder arithmetic and the dashboard's JavaScript. It
is not minimalism for its own sake. The people this is for run a tennis club, not a
build server: some of them are on a locked-down work laptop, some on a machine with
no internet, and all of them are being asked to trust a program with their members'
names. `python3 build.py` and a browser is a promise you can keep on any machine;
`pip install` is not.

A test enforces it. `test_project_complete.py` parses every import in
`matchcentre/`, `tools/` and `build.py` and fails on anything outside
`sys.stdlib_module_names`. If you need something a library would give you, write
the small part of it you actually need, or open an issue and make the case.

**2. No real people in the repository.** Not in code, not in a test fixture, not in
a comment, not in a commit message. No real player, captain, team, club or venue
name, and no scorecard PDF at all — a test checks that none is tracked.

Cards for tests come from `matchcentre/cardwriter.py`, which writes a scorecard PDF
that the reader reads exactly like a real one, or from `tools/demo.py`, which
invents a whole season. Both are how the suite runs on a machine that has never
seen a genuine card. If you need a card shaped like one that broke, describe the
shape and write it with `cardwriter`; that is nearly always quicker than
anonymising a real one, and it leaves a fixture the next person can read.

If you are reporting rather than fixing, the same rule applies to your issue:
`python3 tools/doctor.py --redact your-card.pdf` gives a report you can paste in
public. The README's [Reporting a
problem](README.md#reporting-a-problem) section explains what it does and does not
keep. If you find that it kept something it should not have, that is the one bug
here that must not be reported in public: [SECURITY.md](SECURITY.md) has a private
route for it.

## Running the tests

```bash
python3 -m unittest discover -s tests -t tests
```

About twenty minutes, no network beyond a web server on 127.0.0.1 that the fetcher's
tests start and stop themselves, no installing, and none of your own cards are read.
`-t tests` matters: the test files import each other as top-level modules, so
`-t .` fails on the first import. A single file also runs on its own —
`python3 tests/test_foundation.py` — which is quicker while you are working.

The same command runs on 3.11, 3.12 and 3.13 in `.github/workflows/tests.yml`, on
your branches too if you have forked this. 3.11 is the floor because the config
loader uses `tomllib`.

## Adding a test

A test that passes when the code is broken is worse than no test, because it makes
the next person confident. So the standard here is: **break the thing your test
covers, watch the test fail, put it back.** Invert a comparison, delete a clause,
return a constant. If the suite stays green, the test is not testing what you think
it is.

Say in the pull request what you broke and that the test caught it. That one
sentence is most of the review.

Two habits from the existing suite, both worth copying. Name the test after the
promise rather than the function — `test_a_card_that_lost_a_set_row_is_refused_in_words`
tells you what broke from the failure line alone. And write the reason down where
the assertion is: about half the comments in `tests/` are a sentence saying which
real bug the test was written for, which is what stops someone deleting it later as
redundant.

## Changing what a number means

Anything that touches scoring, the ladder, validation or the readers changes what a
club publishes about real people, so these get read closely.

- Say which scheme or which competition it affects, and cite the rule if there is
  one written down.
- Show it on a demo season rather than describing it:

  ```bash
  python3 tools/demo.py --out /tmp/demo-data --seed 7 --teams 6 --rounds 3
  python3 build.py --data /tmp/demo-data --config /tmp/demo.toml \
                   --out /tmp/before --generated 2026-09-10T12:00:00
  # ... switch to your branch, then:
  python3 build.py --data /tmp/demo-data --config /tmp/demo.toml \
                   --out /tmp/after --generated 2026-09-10T12:00:00
  diff -r /tmp/before /tmp/after
  ```

  The first build writes `/tmp/demo.toml` with everything it worked out behind a
  `#`, so `after` reads the same settings `before` was built from. `--seed` makes
  the cards reproducible, and `--generated` on every build pins the date, so the
  only difference `diff -r` shows is the one you made. Make both builds with
  the same Python: the suite checks that one release on one Python writes the same
  bytes in every folder, time-zone setting and hash seed it tries, and it has never
  compared two Pythons with each other.
- New behaviour that changes existing dashboards belongs behind a config setting
  with a documented default, not in place of the old behaviour. A club that rebuilt
  and got different points from the same cards has no way to tell a fix from a bug.

The two rules the whole codebase is built on, if a design question comes up: **the
cards are the only source of a result** — never a filename, never a folder name,
never the config — and **absence of evidence is never recorded as evidence**. A
missing card is a missing card, not a bye; an unreadable page is a reported
problem, not a zero.

## Documentation

The README is the product as much as the code is; a setting nobody can find does
not exist. If you add one, it goes in `config.example.toml` too — that file is
loaded and checked by the suite, so a preset named there and nowhere in the code
fails the tests.

Screenshots in `docs/screenshots/` are generated, never photographed, so that no
real club's ladder is ever published here:

```bash
python3 tools/screenshots.py
```

That one command needs a browser, which is why it is a maintainer tool and not part
of the project's promises — nothing a club runs needs one. It pins the demo seed and
the timestamp, so re-running it on unchanged code produces byte-identical PNGs and
an empty diff.

## Making a release

What each of the three numbers promises a club is in the README's [versions and
upgrading](README.md#versions-and-upgrading) section; that decides which one moves.
Each change goes into `CHANGELOG.md`'s `[Unreleased]` section as it is made, under
`Added`, `Changed` or `Fixed`. A corrected reading goes under `Fixed` and says out
loud that figures will move, because that is the one patch a club has to look at
rather than take. A release is then a pushed tag, in this order:

1. `python3 tools/release.py prepare X.Y.Z` writes the version into
   `matchcentre/__init__.py`, where the report, `season.json`, both footers and
   `--version` all read it; moves the `[Unreleased]` section under
   `## [X.Y.Z] — YYYY-MM-DD`, dated today unless `--date` says otherwise; and
   rewrites every example of the report's first line that is written out by hand,
   the issue forms among them. It refuses a version that is not newer, an empty
   `[Unreleased]` or a date that does not exist, and writes nothing then. It commits
   nothing, and ends by printing the steps below. A final release that follows its
   own pre-releases, `X.Y.Z` after `X.Y.Z-rc.1`, also gathers their changes under
   its heading, subsection by subsection after whatever `[Unreleased]` holds, so
   that its notes say everything since the last final. The pre-releases' own
   sections stay where they are, below it, as their releases stay on GitHub.
2. Read the new section: `python3 tools/release.py notes` prints it as the release
   page will show it, each wrapped paragraph and item on one line.
3. Run the whole suite, and regenerate the screenshots if anything visible changed,
   with the command in the section above. They are pinned to a seed and a date, so
   an unrelated release leaves them byte-identical and the diff stays empty.
4. Commit, then `git tag -a vX.Y.Z -m "matchcentre-dashboard X.Y.Z"`.
5. `git push origin main`, and last `git push origin vX.Y.Z`. A tag is the one step
   other people may already have fetched by the time you notice it was premature.

Pushing the tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml),
which needs nothing enabled. Its first job runs `tools/release.py check` on the tag,
and fails in seconds when the tag is not `v` and the version in the code, when the
changelog's newest section is not that version, is undated or empty, or when
`[Unreleased]` still holds something the notes would leave out. Then the whole suite
runs, as `tests.yml` runs it on every push. Then `publish` zips the tagged tree with
`git archive` as `matchcentre-dashboard-X.Y.Z.zip` and makes the GitHub Release with
`gh`: the changelog's section is its notes, followed by the zip's SHA-256 and a link
to the run that tested it. A version with a pre-release part, `X.Y.Z-rc.1`, is
published as a pre-release.

To see it first, `python3 tools/release.py check vX.Y.Z` runs the first job's check
on your copy, before you tag, and after tagging
`python3 tools/release.py publish vX.Y.Z --dry-run` writes the zip and the notes
into `dist/`, which git ignores, and prints each `gh` command instead of running it.
Without `--dry-run` it runs them, and refuses to unless `GH_REPO=OWNER/REPO` names
the repository, as the workflow sets it: otherwise `gh` chooses, and in a fork's
clone it chooses the parent.

A re-run from the run's page runs the same tagged commit again, so it mends only a
cause outside that commit: a runner that failed, an outage, a setting or a token,
or a `publish` that stopped half way, which it finishes, since `publish` updates a
release it finds already there and publishes it if it is a draft. A cause in the
commit — a test that fails, the changelog, the version, the tag pushed before the
bump or on the wrong commit — is not mended by re-running. Commit the fix, then
delete the tag, here with `git tag -d vX.Y.Z` and on GitHub with
`git push origin :refs/tags/vX.Y.Z`, and tag the new commit and push it again.
Deleting the tag of a release already made turns that release into a draft, which
the next run's `publish` updates and publishes again. Cancel a run of the old tag
that is still going before you delete it. If one is left going, or re-run later,
its `publish` fails rather than publishing the old commit: it asks GitHub which
commit the tag is now, and refuses when that is not the one it tested.

## What is not in the repository

`notes/` holds the design documents — the format contract, the verification log, the
roadmap — and is gitignored, because the earliest of them discuss the predecessor
project's real club and real players by name. You do not need them: everything they
say that a contributor needs has been moved into docstrings and test comments next
to the code it is about. If you find something you needed and could not find, that
is a documentation bug worth reporting on its own.

## Pull requests

Small and single-purpose is much easier to accept than large and mixed. A refactor
and a behaviour change in one branch means neither can be taken without the other.

There is no CLA, no style bot and no required issue first. Match the surrounding
code: full sentences in comments, a docstring saying what a thing is for rather
than restating its signature, and lines that fit in 79 columns.
