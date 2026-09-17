<!--
Thank you. Two things about this project make review unusual, and knowing them
before you start saves a round trip. Both are in CONTRIBUTING.md in full.
-->

## What this changes, and why


## Checks

- [ ] `python3 -m unittest discover -s tests -t tests` passes, and I said how many
      tests ran.
- [ ] No new dependency. The whole project is the standard library, a test enforces
      it, and it is what lets a club run this by double-clicking.
- [ ] No real player, captain, team, club or venue name in the diff — not in code,
      not in a test fixture, not in a comment. Cards for tests come from
      `tools/cardwriter.py` or `tools/demo.py`.
- [ ] No PDF committed.

## If it changes a number

Anything that touches scoring, the ladder, validation or the readers changes what a
club publishes about real people. Say which of these is true:

- [ ] It cannot change any existing dashboard (refactor, docs, tooling, new code
      behind a new option).
- [ ] It can, and here is the before-and-after on a demo season, plus the reason the
      new figure is the right one.

<!--
python3 tools/demo.py --out /tmp/demo-data --seed 7 --teams 6 --rounds 3
python3 build.py --data /tmp/demo-data --out /tmp/before --config /tmp/demo.toml
  ... then again on your branch, and diff the two season.json files.
-->

## If it adds a test

A test that passes when the code is broken is worse than no test. Break the thing it
covers — invert a comparison, drop a clause, return a constant — check the test
fails, put it back. Say what you broke and that the test caught it.
