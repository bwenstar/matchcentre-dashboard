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

![The ladder page of a dashboard, with the scoring rules that produced it printed above it](docs/screenshots/ladder.png)

---

## Quick start

```bash
python3 tools/demo.py     # invents a fictional club's season of scorecards
python3 build.py          # reads them, writes site/
```

Open `site/index.html`. That is the tool working, on made-up data, in two
commands and about ten seconds.

Then delete `data/demo-season/`, put your own PDFs in `data/`, run
`python3 build.py` again, and read what it printed. The rest of this file is
detail: [where the PDFs come from](#getting-the-pdfs-out-of-match-centre),
[how to name the folders](#where-to-put-them), and — the one thing you do have to
tell it — [which scoring scheme your competition
uses](#scoring-presets-and-picking-yours).

---

## Contents

- [Quick start](#quick-start)
- [What you get](#what-you-get)
- [What it looks like](#what-it-looks-like)
- [What you need](#what-you-need)
- [See it work before you have any cards](#see-it-work-before-you-have-any-cards)
- [Getting the PDFs out of Match Centre](#getting-the-pdfs-out-of-match-centre)
- [Where to put them](#where-to-put-them)
- [The one command](#the-one-command)
- [Reviewing config.toml](#reviewing-configtoml)
- [Scoring presets, and picking yours](#scoring-presets-and-picking-yours)
- [Byes, washouts and rounds nobody played](#byes-washouts-and-rounds-nobody-played)
- [Several seasons](#several-seasons)
- [Why it asks you to confirm the team list](#why-it-asks-you-to-confirm-the-team-list)
- [Why it refuses a card instead of guessing](#why-it-refuses-a-card-instead-of-guessing)
- [Troubleshooting](#troubleshooting)
- [All the commands](#all-the-commands)
- [Checking the tool itself](#checking-the-tool-itself)
- [Reporting a problem](#reporting-a-problem)
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

## What it looks like

Every picture below is a real dashboard built by the two commands in the [quick
start](#quick-start) — but from a season nobody played. `tools/demo.py` invents the
club, the six teams, the eighteen players and their scorecards, so no real person
is on this page. Yours will look like this with your club's names in it.

**Every match, with its set scores and anything unusual the card recorded.**

![The matches page, listing each round's matches and the set scores of every rubber](docs/screenshots/matches.png)

**One team: its home and away split, its recent form, its record in each rubber,
and its head-to-head against every other side.**

![A team page showing form, home and away split, and per-rubber record](docs/screenshots/team.png)

**One player: sets and games, singles and doubles apart, and how the rating printed
on the cards moved through the season.**

![A player page showing sets, games, singles and doubles records and a rating chart](docs/screenshots/player.png)

**Everyone who played, sortable, with the players the tool is unsure about called
out rather than quietly merged.**

![The players page, a sortable table of every player's record](docs/screenshots/players.png)

The `demo` workflow builds the same season and publishes it to GitHub Pages, so a
fork with Pages switched on has a live copy to click around in. Turn it on under
**Settings → Pages → Source → GitHub Actions**; the address is then the one that
workflow prints in its own summary.

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
  unplayed     zero                    a bye or a washout scores nothing, and is not counted as a match played
  format       read from each card     the row bands and marker column of every card
```

Read those lines. They are the settings that decide who is top of the ladder, and
they are the ones no scorecard can tell us. If they are not your competition, see
[Scoring presets](#scoring-presets-and-picking-yours) — and, for the `unplayed`
line, [Byes, washouts and rounds nobody
played](#byes-washouts-and-rounds-nobody-played).

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
  ladder       in progress: teams have played unequal numbers of matches

    #  team                  P  W  L  D     pts    avg      games
    1  Brindle Wombats       5  4  1  0    27.0   5.40    90-77
    2  Brindle Bunyips       5  3  2  0    26.5   5.30   105-72
    3  Brindle Quolls        5  3  2  0    25.5   5.10    95-88
    4  Brindle Rosellas      4  2  2  0    18.6   4.65    66-73
    5  Brindle Magpies       4  1  3  0    15.8   3.95    58-69
    6  Brindle Kookaburras   5  1  4  0    19.4   3.88    74-109
  problems     0 error(s), 6 warning(s) -- see below
```

Two teams show `4` in the `P` column and the rest show `5`, because the demo's
round 5 includes a match called off — which is what the `in progress` note is
saying. That is the `unplayed = zero` line above doing its job: a round nobody
played adds nothing to either team, so until that match is rescheduled and its
card copied in, the ladder's order is not settled. `avg` is the column to compare
in the meantime; it is points per match counted, so it is fair across an unequal
`P`.

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

## Byes, washouts and rounds nobody played

You do not have to type in your draw to get byes right. There are two questions
here and they are separate: **who had the week off**, and **what that is worth**.

A third case needs nothing from you at all: a match that *does* have a card, ticked
`Match Cancelled`. That one is read off the page and handled on its own — see [A
round was called off, and its card has scores on
it](#a-round-was-called-off-and-its-card-has-scores-on-it).

### Who had the week off

Most of the time the tool works this out. Once you have confirmed the team list —
see [Why it asks you to confirm the team
list](#why-it-asks-you-to-confirm-the-team-list) — a round whose cards account for
every team but one has found its bye, and it is credited to the right team without
you doing anything.

What it cannot see is a round that left no cards behind:

- a week nobody played at all — a long weekend, a washed-out round, a bye week for
  the whole grade;
- a round where *two or more* teams are missing, because that is far more likely
  to be a card that never got exported than two byes in one week;
- a round of a five-team grade where the missing card and the bye happen together.

For those, tell it. One line per team, in the rounds column your association
already publishes — here for a five-team grade, where somebody rests every week
and round 6 was rained off entirely:

```toml
[competitions.saturday-division-a.byes]
"Brindle Bunyips"     = [1, 6]
"Brindle Kookaburras" = [4, 6]
"Brindle Magpies"     = [5, 6]
"Brindle Quolls"      = [3, 6]
"Brindle Rosellas"    = [2, 6]
```

That is the whole job — find the `B` column on the association's sheet and copy it
down. You do not have to enter any match, any fixture or any date. Rounds are
written as they appear on the cards, so `[1, 6]` and `["SF"]` are both fine, and a
single round needs no brackets: `"Brindle Quolls" = 3`.

Anything you leave out is still worked out from the cards, exactly as before, so
there is no penalty for filling in only the weeks the tool got wrong. A team you
list that is not in `teams` stops the run and says so, because a bye is the one
thing that pays a team for playing nothing, and a misspelt name there would pay a
team that does not exist.

The first run pre-fills this section for you with every bye it worked out,
**commented out**, so you can check them against the sheet without typing them
again. Uncomment nothing and nothing changes.

### What a bye is worth

Associations disagree about this, and the disagreement is worth a line in
`config.toml`:

```toml
[rules]
unplayed = "average"
```

| `unplayed` | a bye or a washout | counts as a match played |
|---|---|---|
| `zero` | scores nothing | no |
| `average` | is credited the team's own average over the matches it did play | yes |

`zero` is the default, under every preset, because it is the convention that needs
no explaining: a round nobody played adds nothing. Averaging is the more surprising
of the two — it puts points on the board for a night off — so the line above is
what you write if your association does that. Setting it under `[rules]` applies to
every competition, and a `[competitions.<id>]` section can override it again for
one grade.

**This never changes the ladder order.** Crediting a team its own average cannot
move its average, and the average is what the ladder is sorted on. All that
changes is the points total printed against a team that has had a week off — which
is exactly the number a club compares against its association's sheet. So if your
order is right but your totals sit *below* the sheet's, and the teams that differ
are the ones that have had a bye or a washout, this is the first line to try.

The average column is rounded the way a spreadsheet rounds — a half goes up — so
`6.225` prints as `6.23`, not as the `6.22` most programming languages would give
you. That is deliberate, and it is why the column can be compared against a
published ladder digit for digit.

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
- the front page gains a season picker, grouped by season and newest first;
- each season keeps its own ladder, its own teams and its own player pages.

Nothing is added together across seasons — not a ladder, and not a player's record.
A player who appeared in three of your seasons has three separate pages, one per
season, and the picker is how you get between them. Career totals spanning seasons
are not built yet; the season boundary is the one thing every figure on a page can
be traced to a card for, and totalling across it would mean a page whose numbers no
single season's cards account for.

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

### The report says a card prints `VS` instead of a result

The match was still read, still scored and still on the ladder. Check it on the
dashboard if you like — it is there with its games, its sets and its points.

Match Centre prints `DEFEATS` or `DEF. BY` between the team names when it holds a
result for the fixture and `VS` when it does not, usually because the scores were
entered but never confirmed at the association's end. The set rows are on the card
either way, and the set rows are what this tool scores from; the banner is only one
of the three places a card repeats its own result.

What the warning tells you is which check could not run. A card printing `VS`
normally carries no `Match Winner` tick either, so its printed `TOTAL` row is left
as the only confirmation of its set rows — one is enough to trust them, and if that
one had disagreed you would have an error instead. If you would rather have all
three, export the card again once the association has finalised the result.

A match that genuinely was not played is a different line in the report:
`no-result`, which names the washout or the cancellation instead. See [A round was
called off, and its card has scores on
it](#a-round-was-called-off-and-its-card-has-scores-on-it).

### A round was called off, and its card has scores on it

The report says so under `no-result`, naming the card:

```
  no-result
      wednesday-division-3: R5 brindle-magpies v brindle-rosellas: recorded as cancelled, so it is not counted in the ladder.
```

That is the report telling you the card was read and then set aside, and both
halves of that matter. It knows because the card itself says so: Match Centre
prints a ticked `Match Cancelled` box on it, which is the only thing on a page that
can distinguish a night called off from a night played. A washed-out match with no
such tick reads as an ordinary one, and the fix is to mark it cancelled in Match
Centre and export the card again.

A washed-out night often stops partway. Two rubbers finish, a third is level at
5-5, the rain comes, and Match Centre keeps whatever was entered — so the card
arrives with real games on it and no result. Those games are not a partial result
to be counted at reduced strength. They belong nowhere, so nothing on the card is
counted anywhere: not a game, not a set, not an appearance, not a match played.
The two teams show one fewer in the ladder's `P` column than the teams who played
that night, and their averages are worked out over the matches they did play.

This is not the same as a match that was played to the end with one rubber left
unfinished — a set abandoned at 4-4, a pair who had to leave. That match happened,
so its games, sets and appearances all count, and it takes a ladder result too as
long as enough rubbers finished (`min_sets_for_result` under `[rules]`, three of
four by default). The line between the two is not how much of the card is filled
in; it is whether the night was played at all.

There is nothing to fix here. The one thing worth checking is that the ladder's
`P` column matches your association's sheet — if the sheet has a number there for
the washed-out night, your association pays for it, and that is the `unplayed`
line: see [What a bye is worth](#byes-washouts-and-rounds-nobody-played).

### The report says a card prints `Unspecified player`

The warning is `players-unrecorded`, and it counts them for you: how many name cells
on the card print the placeholder, and how many of the card's rubbers those cells
are spread across. One line per card, however many cells it has.

`Unspecified player` is what Match Centre prints where a name has not been entered.
It is not somebody's name, so it is not read as one: no player is created, nothing
is credited to anyone, and the rubber's games still count if the match was played.
The warning exists because the alternative — dropping the cell without saying so —
looks identical to a card that had no such row at all.

What to do depends on the last clause of the message:

- **`this match was called off`** — nothing. The night never happened, the names
  were never going to be entered, and the card contributes nothing regardless.
- **`those rubbers have games but nobody to credit them to`** — the match *was*
  played, and somebody's matches are missing from their record. Enter the players
  in Match Centre, export that card again, replace the file in `data/` and re-run.
  The team's games and points are already right; it is the player pages that are
  short.

### A team was awarded a bye it should not have had, or a round is short

Confirm the team list in `config.toml` — remove the `#` from the `teams = ` line.
Until you do, no bye is credited at all, and the report names the short round
instead. See [Why it asks you to confirm the team
list](#why-it-asks-you-to-confirm-the-team-list).

If the list is confirmed and a bye is still on the wrong team, or a week nobody
played is missing entirely, state the byes yourself in the
`[competitions.<id>.byes]` table: a stated bye wins over a worked-out one. See
[Byes, washouts and rounds nobody
played](#byes-washouts-and-rounds-nobody-played).

### My points totals are close to the association's sheet but not equal

If the teams are in the same order, the difference is almost always what a bye or a
washout is worth, and that is one line: `unplayed` under `[rules]`. See [What a bye
is worth](#byes-washouts-and-rounds-nobody-played).

If one team alone is out and it has had a week off, check that week's byes. If one
team alone is out and it has not, compare its matches — that is a card, not a
convention.

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
dashboard's "players to check" list is where these surface — see below.

### The dashboard has a "players to check" list. Why are these people on it?

Because they appear on one or two of the scorecards their team played, and nothing
else. It is a list of names worth a glance, not a list of errors, and most weeks
every row on it is perfectly fine: a fill-in from another grade, a late arrival, a
player who came back from injury for a night.

It is there because that is also exactly what **one player entered under two
spellings** looks like. `A Whitlock` on three cards and `Anna Whitlock` on two are
two players as far as any program can tell, each with half a record, and neither
half looks wrong on its own. The only way to see it is to see them side by side.

So: read the **Rounds** column first. Two spellings of one person never share a
round — one card spells it one way, another card the other way, and no card does
both. Two rows whose rounds interleave without overlapping are worth a look; two
rows that both played round 3 are two different people.

If two rows are the same person, map one spelling onto the other under `[aliases]`
in `config.toml` and re-run; the example config shows the form. Nothing is merged
for you, because merging two players who are not the same person is not something
that can be undone by looking at the dashboard.

The **Note** column narrows it further. `on no team's roster` means the name is in
a set row but in neither roster block, which is what a misspelling in a set row
looks like — the report names those too, as `unknown-player` or `player-no-team`.
`played outside their own roster` is a fill-in, and is normal. A dash means neither,
and those rows are the ones the Rounds column is for.

The list stays hidden until somebody has had four matches available, because two
rounds into a season everybody has played once or twice and the list says nothing.

Being on the list is not itself a problem: it changes no score, moves nothing on the
ladder, and adds no line to the report on its own. Read it once a season, fix any
split names you find under `[aliases]`, and leave the rest.

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
python3 tools/doctor.py --redact C.pdf  # the same report with every name, team,
                                        # venue and date replaced by its shape,
                                        # so it can be pasted into an issue

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
| `test_doctor.py` | `tools/doctor.py --redact` — that no name survives it and every measurement does |
| `test_hostile_input.py` | Truncated, corrupt and not-really-PDF files — that each is refused in words, and none is read as a different match |
| `test_project_complete.py` | That a fresh copy is complete, generic and empty of data |

A single file runs on its own, which is quicker while you are working:

```bash
python3 tests/test_foundation.py          # one line per test, and a name that
                                          # says what broke
```

The same command runs on every push here, on Python 3.11, 3.12 and 3.13, in
[`.github/workflows/tests.yml`](.github/workflows/tests.yml). There is no
`pip install` step in that file either, which is how the "Python is the whole list"
promise above stays true rather than merely intended: a dependency creeping in
fails the workflow on the import. If you fork this, the same workflow runs on your
branches for free.

---

## Reporting a problem

**Please do not attach the scorecard.** It has both teams, every player who was on
court, their ratings and the captains on it, and an issue on GitHub is public
forever. Run this instead and paste what it prints:

```bash
python3 tools/doctor.py --redact path/to/the-card.pdf
```

`--redact` replaces every name, team, club, venue, date and path with its shape —
`Priya Halloran` becomes `<2 words, 15 chars>` — and keeps every measurement,
score, count and structural fact. That is the half needed to diagnose a card and
the half that identifies nobody. Read it before you paste it.

Two things are worth including whatever the problem is. The first line of the
report `build.py` prints, which says which version and which Python produced what
you are describing. And, if a number looks wrong, the `== rules in force` block —
a ladder computed under the wrong scoring preset is not an error but a plausible,
wrong table, and it is the commonest cause by a distance.

There are issue forms for [a card that will not
read](.github/ISSUE_TEMPLATE/card-not-read.yml) and for [a number that looks
wrong](.github/ISSUE_TEMPLATE/wrong-number.yml) that ask for exactly this.
[CONTRIBUTING.md](CONTRIBUTING.md) covers sending a fix rather than a report.

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
