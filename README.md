# matchcentre-dashboard

Turn a folder of match scorecards into a season dashboard your club can read.

The scorecards have to be the PDF ones exported from
**<https://matchcentre.tennis.com.au>**. That is the one supported source, and it
is the reason this tool exists. Match Centre already holds every set score of
every match your club plays, one A4 page per match, and it will hand you those
pages as PDFs. What it will not do is add them up into a ladder, a per-team page
and a per-player page. This does that.

You copy the PDFs into `data/`, run one command, and open the page it writes.

```
data/                      python3 build.py            site/index.html
  round-1/*.pdf     ->     (reads every card)     ->   ladder, teams,
  round-2/*.pdf                                        players, matches
```

Nothing is typed in twice. Every score, date, round, division, team name, player
name and rating comes off the cards themselves.

---

## Contents

- [What you get](#what-you-get)
- [What you need](#what-you-need)
- [See it work before you have any cards](#see-it-work-before-you-have-any-cards)
- [Getting the PDFs out of Match Centre](#getting-the-pdfs-out-of-match-centre)
- [Where to put them](#where-to-put-them)
- [The one command](#the-one-command)
- [Reviewing config.toml](#reviewing-configtoml)
- [Scoring presets, and picking yours](#scoring-presets-and-picking-yours)
- [Several seasons](#several-seasons)
- [Why it asks you to confirm the team list](#why-it-asks-you-to-confirm-the-team-list)
- [Why it refuses a card instead of guessing](#why-it-refuses-a-card-instead-of-guessing)
- [Troubleshooting](#troubleshooting)
- [All the commands](#all-the-commands)
- [Checking the tool itself](#checking-the-tool-itself)
- [How it works, briefly](#how-it-works-briefly)
- [Licence](#licence)

---

## What you get

One dashboard per competition, written into `site/`, plus a front page that lists
them. It is plain HTML, CSS and JavaScript in a folder. Double-click
`site/index.html` and it opens. There is no web server, no login, and no internet
connection involved at any point.

Each competition's dashboard is built from that competition's own cards and shows:

- **the ladder** — played, won, lost, drawn, points, points average, games for and
  against, and each team's position;
- **each team** — home and away split, recent form and current streak, its record
  in each rubber of the match (first doubles, second singles, and so on), and its
  head-to-head record against every other team;
- **each player** — sets and games won and lost, singles and doubles separately,
  win percentage, the rating printed on the cards and how that rating moved
  through the season, and which matches they filled in for another team;
- **every match** — the full set scores, who played in each rubber, the court, the
  date, and any forfeit, retirement, cancellation or dispute the card recorded;
- **the rules in force** — which scoring scheme, which set length and which match
  shape produced these numbers, stated on the page rather than buried in a file;
- **anything the tool had to guess**, marked as a guess.

Beside each dashboard it writes `season.json`, the same numbers as plain data, in
case you want to do something else with them.

Each competition folder is self-contained. You can zip one and email it, and it
opens on a machine that has never heard of this tool.

---

## What you need

**Python 3.11 or newer.** That is the whole list.

- No `pip install`. Nothing is downloaded. The tool uses only what comes with
  Python.
- No build step, no compiler, no Node, no database.
- No web server. The dashboard is opened as a file.
- No internet access, once you have the PDFs.

Check your version:

```bash
python3 --version
```

If that says 3.10 or lower, install a newer Python and run `python3.11 build.py`
instead of `python3 build.py`. The tool checks the version itself on startup and
tells you plainly if it is too old, rather than failing with something cryptic.

macOS and Linux ship Python. On Windows, install it from python.org and use
`py build.py`.

---

## See it work before you have any cards

There is a generator that writes a fictional club's season of scorecards, so you
can watch the whole thing run before you go hunting for PDFs. No real club, no
real person.

```bash
python3 tools/demo.py               # writes fake cards into data/demo-season/
python3 build.py                    # builds a dashboard from them
```

Then open `site/index.html`.

When you are done, delete `data/demo-season/` and your own cards are all that is
left. Do that before you build for real: a fictional division sitting in `data/`
becomes a fictional dashboard sitting next to your real one.

---

## Getting the PDFs out of Match Centre

Match Centre prints a scorecard for a match, as a PDF: one A4 page, your club
letterhead at the top left, the date and court at the top right, the two team
names across the middle with `DEFEATS` or `LOSES TO` between them, one row per
set, a `TOTAL` row, and a signature block with both teams' player lists at the
bottom. That page is what this tool reads.

**A note on wording.** This tool was written on a machine with no internet
access, so the exact labels on Match Centre's own menus could not be checked, and
they change. So this README will not quote you a menu path it cannot stand behind.
What you are looking for is:

> the option that prints or downloads a **match scorecard** — one A4 page per
> match, with your club letterhead, the set rows and the signature block.

It is usually reached from a fixture or a result, not from the ladder. Depending
on your association's setup it may be described as printing, downloading,
exporting or viewing the scorecard, and it may open the PDF in a new tab for you
to save. Any of those is fine. If your browser opens it rather than saving it,
use its own print-to-PDF or download option to put the file on disk.

Some associations expose the scorecard to team captains and club administrators
only. If you cannot find it at all, ask whoever administers your club's Match
Centre account.

**You have the right file when all of these are true:**

- it ends in `.pdf` and is small — a few tens of kilobytes, not megabytes;
- opening it shows **one match** — not a draw, not a ladder, not a list of
  fixtures;
- your club's name is at the top;
- there is one row per set, with a games score on each side;
- there is a `TOTAL` row under the set rows;
- there are small tick boxes labelled `Match Winner`, `Forfeit`, `Retired`;
- both teams' players are listed at the bottom with their ratings.

**Check one before you fetch fifty.** There is a checker in the box. Point it at
a single saved card:

```bash
python3 tools/doctor.py "path/to/that/card.pdf"
```

It prints what the file is and what it found on the page, and finishes with a
plain verdict. The last lines you want to see look like this:

```
    verdict
      This page reads as a scorecard.
        division      3
        round         1
        date          2026-02-04 Wednesday
        home          BRINDLE ROSELLAS
        away          BRINDLE KOOKABURRAS
        verb          DEFEATS
        sets          4 row(s): 6-2, 6-4, 6-3, 6-3
        format        dssd
        totals row    present
        winner tick   home
        cross-checks  3 available, 0 disagreeing
          totals        agrees  printed games 24-12 and sets 4-0 match the 4 set rows
          verb          agrees  'DEFEATS' agrees with the set rows
          winner-tick   agrees  the home Match Winner tick agrees with the set rows
```

(That is a real run, against one of the fictional cards `tools/demo.py` writes; see
[See it work before you have any cards](#see-it-work-before-you-have-any-cards).)

If that reads back your real teams and your real set scores, the export you chose
is the right one and you can go and collect the rest. If it does not, a different
export is more likely the answer than a change to your process — and the checker
will have said which part of the page it could not find.

One page per file is the normal case. A PDF holding several scorecards, one per
page, is read as well; every page is treated as its own match.

---

## Where to put them

Anywhere under `data/`. The folder is searched all the way down, and **nothing at
all is read from a file name or a folder name**. A card's division, round, date,
day of the week, court, teams, players and scores all come from inside the PDF.

So the layout is for your convenience, not the tool's. Any of these work:

```
data/                          data/                       data/
  round-1/                       tuesday-c/                  everything/
    falcons-v-herons.pdf           r1-1.pdf                    scan001.pdf
    currawongs-v-ibis.pdf          r1-2.pdf                    scan002.pdf
  round-2/                       thursday-a/                   ...
    ...                            r1-1.pdf
```

You can drop a Wednesday Division 3 card and a Saturday Division A card into the
same folder and both land in the right dashboard. Rename files however suits you.
`.pdf` and `.PDF` are both found.

There is one exception, and it is opt-in: if you turn on multi-season mode, the
**first** folder level under `data/` is taken as the season name. See
[Several seasons](#several-seasons).

**Keep only match scorecards under `data/`.** Your association's draw PDF, rules
PDF and team-list PDF are not scorecards, and a PDF under `data/` that cannot be
read as one is reported and makes the run finish with an error. That is
deliberate: a scorecard that quietly disappears is the most expensive thing that
can go wrong here, because the ladder still looks perfectly reasonable and a team
is simply short a match. Keep the other documents in a folder of their own,
outside `data/`.

---

## The one command

```bash
python3 build.py
```

That is it. It reads every card under `data/`, writes the dashboards into `site/`,
and prints a report. On the very first run — when there is no `config.toml` yet —
it also writes one for you, filled in from what your cards turned out to say, and
then builds the dashboard anyway. You never have to configure anything before
seeing whether the thing works.

The report starts with the rules it used:

```
== rules in force ============================================================
  scoring      games-bonus             win 4, draw 3, loss 2, 0.1 per game; decided on games then sets
  sets         first-to-6              first to 6
  format       read from each card     the row bands and marker column of every card
```

Read those three lines. They are the settings that decide who is top of the
ladder, and they are the ones no scorecard can tell us. If they are not your
competition, see [Scoring presets](#scoring-presets-and-picking-yours).

Then what it found:

```
== cards =====================================================================
  source       data
  pages seen   15
  scored       15
  refused      0
  competitions 1
  seasons      single (every card is one season)
```

`pages seen` and `scored` should be the same number, and `refused` should be zero.
Then one block per competition, with the ladder printed in the terminal so you can
sanity-check it against the one your association publishes:

```
== wednesday-division-3 ======================================================
  name         Wednesday Division 3
  rules        games-bonus / first-to-6 / doubles-singles-doubles
  cards        15 across round(s) 1, 2, 3, 4, 5
  teams        6 (derived)
  rounds       5 played; season length not declared, so no percentage is shown
  draw         cards (card)
  ladder       complete: every team has played the same number

    #  team                  P  W  L  D     pts    avg      games
    1  Brindle Wombats       5  4  1  0    27.0   5.40    90-77
    2  Brindle Bunyips       5  3  2  0    26.5   5.30   105-72
    3  Brindle Quolls        5  3  2  0    25.5   5.10    95-88
    4  Brindle Rosellas      4  2  2  0    23.2   4.65    66-73
    5  Brindle Magpies       4  1  3  0    19.8   3.95    58-69
    6  Brindle Kookaburras   5  1  4  0    19.4   3.88    74-109
  problems     0 error(s), 5 warning(s) -- see below
```

Then a review list of everything it worked out rather than being told, each with
the exact lines to paste into `config.toml` if you want to confirm it. Then any
problems. Then the last word:

```
== result ====================================================================
  wrote        8 file(s) under site
  open         site/index.html  (double-click works; no web server needed)
  exit 0: all 15 card(s) scored, no errors
```

`exit 0` with no errors is the pass condition. Anything else is explained in the
report above it, in the section named for the thing that went wrong.

Adding next week's round is the same command again. Copy the new cards in, run
`python3 build.py`, and the dashboard is rebuilt from scratch from everything in
`data/`. Nothing accumulates, so there is no state to get out of step and no way
to double-count a card you copied in twice under two names — that is reported as a
duplicate, naming both files.

---

## Reviewing `config.toml`

The first run writes `config.toml`. You do not have to touch it. The dashboard
already works. What the file does is let you replace the tool's guesses with what
you actually know, and each guess it makes is marked as a guess on the dashboard
until you do.

Every value the tool worked out for itself is written into that file **commented
out**, with a note saying what confirming it would change. Adopting one is a
deliberate act: you delete the `#`. Here is a real example from a first run:

```toml
# The full list of teams in this competition, as read from
# the cards. Check it, then remove the # — that tells the
# program the list is complete, which lets it handle byes and
# flag a misspelt team instead of inventing a new one.
# teams = ["Brindle Bunyips", "Brindle Kookaburras", "Brindle Magpies", ...]
```

Read the list. If it is your six teams, delete the `#` at the start of the
`teams = ` line and re-run. If there is a seventh team on it you have never heard
of, you have just found a typo on a scorecard — see
[Why it asks you to confirm the team list](#why-it-asks-you-to-confirm-the-team-list).

The four things most worth confirming, in order:

1. **`teams`** — the team list. Unlocks byes, and catches misspellings.
2. **`scoring` and `sets`** under `[rules]` — how your competition awards points,
   and how long a set is. These cannot be read off a card and they move the
   ladder.
3. **`rounds_total`** — how many rounds the season runs. Without it the dashboard
   says "4 rounds played" and shows no percentage, rather than implying the season
   is finished.
4. **`season`** — what your association calls this season, for the heading.

Everything else is polish: how you spell each team, captains, home courts, the
published draw, and merging two spellings of one person's name into one player.

`config.example.toml` in this folder is a fully commented example with every
setting and its alternatives, for reference. You do not need to copy it; your own
`config.toml` is written for you and is easier to start from.

Nothing in `config.toml` can change a score. It supplies names, labels, the team
list, the draw and the rules. Set something wrong in it and you get a wrong label,
never a wrong result. Results only ever come from the cards.

---

## Scoring presets, and picking yours

No scorecard says how its competition turns sets into ladder points, so this is
the one thing you have to tell the tool. It is printed at the top of every run and
shown on the dashboard, so a club running the wrong one can see that without
reading any code.

Set it in `config.toml`:

```toml
[rules]
scoring = "games-bonus"
sets = "first-to-6"
```

**`scoring`** — how a match is worth points, and how a match is decided:

| preset | win / draw / loss | bonus | a match is decided on |
|---|---|---|---|
| `games-bonus` | 4 / 3 / 2 | 0.1 per game won | total games, then sets |
| `sets-first` | 2 / 1 / 0 | none | sets won, then games |
| `rubbers` | points are the sets you won | none | sets won, then games |
| `simple` | 3 / 1 / 0 | none | sets won, then games |

`games-bonus` is the default. Under it every game played counts for something, so
a narrow loss outscores a heavy one and a dead rubber still matters; the ladder is
ordered on points *average*, so a team that has played fewer matches is not
punished for it. `sets-first` is the common pennant arrangement: whoever won more
rubbers won the match. Pick the row that matches how your association's published
ladder behaves.

**`sets`** — when a set counts as finished. This one is easy to overlook and it
matters:

| preset | a set is |
|---|---|
| `first-to-6` | first to 6 games, 5-5 decided at 6 |
| `advantage-6` | first to 6 by two, no upper limit |
| `six-game-tiebreak` | first to 6 by two, tiebreak at 6-all, so 7-6 is a set |
| `fast4` | first to 4 |
| `short-sets-4` | first to 4 by two, no upper limit |
| `pro-8` | a single set to 8 |
| `pro-8-tiebreak` | a single set to 8, tiebreak at 8-all |
| `pro-10` | a single set to 10 |

`6-3` looks the same on the page whether the set was played to 6 or to 8, which is
why the tool cannot work this out. Get it wrong and the damage is quiet in both
directions: under `first-to-6`, a FAST4 club's `4-2` reads as a set still in
progress and its ladder comes out blank; under `pro-8`, a `6-3` reads as finished
when the players were still on court.

The **shape** of a match — how many sets, and which of them are doubles — is a
different matter: that *is* printed on every card, in the marker column beside
each set row, so it is read off your cards and you do not need to set it.

---

## Several seasons

By default every card in `data/` is treated as one season. If you want to keep
past seasons alongside the current one, turn on multi-season mode:

```toml
[build]
seasons = "multi"
```

**In multi-season mode, the first folder level under `data/` is the season.** That
is the one and only thing this tool ever reads from a path, and it is opt-in
precisely because it is an exception to the rule.

```
data/
  2026-winter/            <- season "2026-winter"
    round-1/*.pdf
    round-2/*.pdf
  2026-summer/            <- season "2026-summer"
    *.pdf
  2025-winter/            <- season "2025-winter"
    *.pdf
```

Everything else still comes from the cards. The folder name only says which
season; the division, the round, the date and the scores are read off the page as
always.

What changes:

- output moves to `site/<season>/<competition>/`, so five years of tennis is five
  folders rather than fifty;
- the front page gains a season picker;
- players get career totals across seasons as well as per-season figures;
- each season keeps its own ladder. Seasons are never added together into one.

Give each folder a readable name in `config.toml`:

```toml
[seasons]
"2026-winter" = "2026 Winter Season"
"2025-winter" = "2025 Winter Season"
```

Name the folders so that they sort sensibly — `2026-winter`, not `winter-2026` —
and the picker will list them newest first.

One thing to know before you switch: turning multi-season mode on changes the
internal name of each competition, because the season becomes part of it. If you
have already written `[competitions.*]` sections by hand, each needs its section
name updated and a `season_key` line adding. The run tells you exactly this if it
finds a section that no longer matches anything.

---

## Why it asks you to confirm the team list

Because the tool cannot tell the difference between a team that had the week off
and a card that never arrived.

Say your division has six teams and plays three matches a night. One week only two
cards turn up. Either a team had a bye, or somebody forgot to export a card. From
the cards alone those two situations are identical. Guess "bye" and the missing
team is credited for a match it actually played and possibly lost; the ladder still
looks completely reasonable, and nothing warns you.

So the tool does not guess. With no confirmed team list it says "this round is
short of cards" and names the round, and it never credits a bye. Once you have
read the list of teams in `config.toml` and deleted the `#`, the list is closed —
the tool now knows all six teams — and a round with a team missing can be credited
as a bye to the right team, correctly.

Confirming the list buys a second thing. A team name is matched by how it is
printed on the card. If one card is entered as `WILLOWBANK FALCOMS`, that is a
team the tool has never seen. With an open list, all it can do is add a seventh
team and mention that it looks a lot like `falcons`. With a confirmed list, an
unrecognised team stops the run and tells you which card it is on. That is how a
misspelt team gets caught in the week it happens instead of at the end of the
season.

---

## Why it refuses a card instead of guessing

Because a guess here once produced a scoreline that was exactly backwards.

The set table on a scorecard is a grid, and the tool works out where the columns
are by measuring the table's own printed rules — the thin vertical lines. That is
how it knows which games score belongs to the home team and which to the away
team. If it cannot find those lines, it has two options: fall back to where the
columns usually are, or stop.

Falling back to where the columns usually are was measured turning `6-2` into
`2-6` on a card whose layout differed. The ladder looked entirely plausible.
Nothing was flagged. The reversal was only caught because those cards happened to
print a `TOTAL` row that disagreed with it.

So now it stops. A card whose structure cannot be read is **refused**, by name,
with the reason, and the run finishes with an error rather than a quietly wrong
number. A refused card is a visible problem you can fix in five minutes. A silently
reversed one is a wrong ladder nobody notices.

The same principle applies after a card is read. A scorecard states its result
several times over: the individual set scores, the `TOTAL` row, the `DEFEATS` or
`LOSES TO` verb between the team names, and the `Match Winner` tick. The tool
checks the set scores against every one of those that the card prints. If they all
agree, the numbers are trusted. If one of them disagrees, that is an error naming
both readings, because something on that page was misread and the last thing you
want is for the tool to pick a winner between them.

This is also why the report insists that every PDF under `data/` be accounted for,
and why the run finishes with an error if even one is not.

---

## Troubleshooting

**Start here.** The tool's own report names every problem it found, in a section
titled for it, with the file at fault. Read it before anything else. When a card is
refused, the report gives you the exact command to investigate it.

### A card was refused

```bash
python3 tools/doctor.py "data/round-3/that-card.pdf"
```

That prints what the file is, what the page contains, which header fields it could
find, what the reader made of it, and a plain verdict. It is deliberately more
forgiving than the builder, so "the checker found a date and a division but the
build refused the page" is itself useful information — it narrows the problem down
to one part of the page.

Common causes, in the order they are worth checking:

| The verdict says | Usually means |
|---|---|
| not a PDF | the file is a web page or an image that was saved with a `.pdf` name |
| no set table found | it is not a scorecard — a draw, a ladder, a fixture list or the rules |
| encrypted, or a feature this reader does not support | the PDF was re-saved or password-protected by another program; export it again from Match Centre |
| no text could be recovered | the PDF is a scan or a photo of a printout, not an export. There is no text in it to read |
| the column rules could not be read | the page layout is not one this tool can measure. Send the file, or its verdict, to whoever maintains this |

The checker exits 0 when a page reads as a scorecard and 1 when it does not, so
you can loop over a folder:

```bash
find data -iname '*.pdf' -print0 |
  while IFS= read -r -d '' f; do
    python3 tools/doctor.py "$f" >/dev/null || echo "BAD $f"
  done
```

### A team was awarded a bye it should not have had, or a round is short

Confirm the team list in `config.toml` — remove the `#` from the `teams = ` line.
Until you do, no bye is credited at all, and the report names the short round
instead. See [Why it asks you to confirm the team
list](#why-it-asks-you-to-confirm-the-team-list).

### There is a team on the ladder that does not exist

Almost always a misspelling on one card. The report names it and suggests the team
it resembles. Fix it in Match Centre, export that card again, replace the file, and
re-run. Confirming the team list turns this from a suggestion into an error, which
is what you want once you have checked the list.

### The ladder order does not match my association's published one

Check the `scoring` and `sets` lines the run prints first. Those two decide the
order, and neither can be read off a card. See [Scoring
presets](#scoring-presets-and-picking-yours).

If they are right and the order still differs, compare the points column team by
team. A single team out of place is usually one match not counted — look for that
team in the warnings. A whole ladder in a different order is the wrong preset.

### The ladder says "in progress"

That is not an error. It means teams have played different numbers of matches, so
the order is a snapshot rather than a settled table. It goes away by itself once
everyone has played the same number.

### The season looks finished when it is not

Set `rounds_total` in `config.toml` to the number of rounds your season actually
runs. Until you do, the dashboard says "N rounds played" and shows no percentage,
on purpose: working the season length out from the rounds played means a season
three rounds into ten reads as complete.

### The same player appears twice, with half their matches each

Two spellings of one name across cards. Add a line under `[aliases]` in
`config.toml` mapping one onto the other; the example config shows the form. The
dashboard lists anyone who appears only once or twice under "players to check",
which is where these usually surface.

### Nothing was found

```
  No scorecards were read from data.
```

Either `data/` is empty, or the files in it are not scorecards. Check that the
files are the PDFs from Match Centre, then run the checker on one of them.

### It says my Python is too old

Install Python 3.11 or newer and run `python3.11 build.py`. The message names your
current version and the path to it, so you can tell which Python it found.

### I want to check something without writing anything

```bash
python3 build.py --check
```

Reads everything, validates everything, prints the whole report, writes no files.

---

## All the commands

```bash
python3 build.py                        # data/ -> site/, the normal run
python3 build.py --check                # read and validate; write nothing
python3 build.py --data /some/folder    # read cards from somewhere else
python3 build.py --out /some/folder     # write the dashboard somewhere else
python3 build.py --config other.toml    # use a different config file
python3 build.py --quiet                # print only problems and the result
python3 build.py --generated 2026-09-10T00:00:00
                                        # pin the "generated" timestamp, so an
                                        # unchanged season rebuilds identically
python3 build.py --discover --force     # rewrite config.toml from the cards,
                                        # discarding what you have declared in it

python3 tools/doctor.py CARD.pdf        # explain one PDF, and why it did or
                                        # did not read as a scorecard
python3 tools/doctor.py --page 2 F.pdf  # look at one page of a multi-page PDF

python3 tools/demo.py                   # write a fictional season into data/
python3 tools/demo.py --clean           # replace a previous demo
```

Every command takes `--help`.

`--discover --force` is a deliberate escape hatch, not part of the normal
routine: it replaces `config.toml` with what the cards say, so anything you had
declared in it is lost. It refuses to run without `--force` for that reason.

`--generated` exists so that rebuilding the same cards produces byte-identical
output. That matters if you keep `site/` or `season.json` in version control:
without it, the only thing that changes between two builds of the same data is a
timestamp, and real changes get lost among them.

---

## Checking the tool itself

The test suite ships with the tool. One command runs all of it, and it takes about
fifteen seconds:

```bash
python3 -m unittest discover -s tests -t tests
```

Nothing needs installing, no network is touched, and none of your cards are read.
Every scorecard the tests use is generated on the spot — the tool can write a
scorecard PDF as well as read one, which is also what `tools/demo.py` uses — so
the suite runs the same on a machine that has never seen a real card.

Worth running in two situations. Before trusting a ladder, if you want to see the
arithmetic checked against worked examples rather than take it on faith. And after
editing anything, because most of these tests exist to catch a specific way of
being quietly wrong:

| File | What it holds you to |
|------|----------------------|
| `test_foundation.py` | Scoring, slugs, formats, ladders — hand-computed answers |
| `test_parsing.py` | Real PDFs in and out, including damaged and unusual ones |
| `test_pipeline.py` | Cards to finished dashboard, end to end |
| `test_web_assets.py` | The page: that every promised panel is built and placed |
| `test_cli.py` | `build.py` as a program — exit statuses, output, rebuilds |
| `test_project_complete.py` | That a fresh copy is complete, generic and empty of data |

A single file runs on its own, which is quicker while you are working:

```bash
python3 tests/test_foundation.py          # one line per test, and a name that
                                          # says what broke
```

---

## How it works, briefly

1. **Read.** Every PDF under `data/` is opened and each page is read as one
   scorecard. The tool measures the printed table — the vertical rules that make
   the columns, the bands that make the rows, the small squares that make the tick
   boxes — and reads the text out of the cells that geometry defines. It does not
   assume where anything is on the page. A page it cannot measure is refused by
   name.
2. **Check.** Each card's set scores are checked against the other places the same
   card states its result: the `TOTAL` row, the `DEFEATS` / `LOSES TO` verb, the
   `Match Winner` tick. Agreement means the reading is trusted. Disagreement is an
   error that names both readings.
3. **Group.** Cards are sorted into competitions by the division printed on them
   and the weekday of their own date. Teams and players are identified by name, not
   by any number, so a card arriving late or a team being added does not shuffle
   anything.
4. **Score.** Sets become match results and ladder points, using the scoring
   scheme and set length from `config.toml`. Every card that could not be read, and
   every value that had to be worked out rather than read, is recorded.
5. **Write.** One folder per competition containing the page, its data and its
   assets, plus a front page listing them all. Same cards in, same files out.

Two rules run through all of it. **The cards are the only source of a result** —
never a file name, never a folder name, never the config file. And **absence of
evidence is never recorded as evidence** — a missing card is a missing card, not a
bye; an unpublished draw is unknown, not a season of zero length; an unreadable
page is a reported problem, not a zero.

`data/README.md` covers the data folder in more detail. `config.example.toml`
documents every configuration setting.

---

## Licence

MIT. See [LICENSE](LICENSE).

Your scorecards contain the names of real people. They stay on your machine: this
tool sends nothing anywhere, and a fresh copy of it ships with an empty `data/`
folder and no club's data in it. If you publish the built `site/` folder anywhere,
remember that it contains those names too.
