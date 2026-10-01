# data/

Your scorecards go here.

They must be the PDF scorecards exported from
<https://matchcentre.tennis.com.au> — one A4 page per match, with your club
letterhead, the set rows, the `TOTAL` row and the signature block. See the main
[README](../README.md) for how to get them and how to check one before you fetch
the rest.

## The folder is empty on purpose

A fresh copy of this tool ships with nothing in here but this file. The
scorecards belong to your club and name real people, so shipping somebody else's
would publish their names and build you a dashboard of a competition you have
nothing to do with.

## How to organise it

However you like. This folder is searched all the way down, and **nothing is read
from a file name or a folder name**. Each card's division, round, date, day,
court, teams, players and scores are read from inside the PDF.

```
data/
  round-1/
    falcons-v-herons.pdf
    currawongs-v-lorikeets.pdf
  round-2/
    ...
```

One folder per round is a common choice. One flat folder works just as well. So
does one folder per division. Rename the files however suits you; `.pdf` and
`.PDF` are both found.

Keep only match scorecards in here. Your association's draw, rules and team-list
PDFs are not scorecards, and a PDF in this folder that cannot be read as one is
reported and makes the run finish with an error — deliberately, because a
scorecard that vanishes quietly leaves a ladder that still looks reasonable and a
team one match short. Put those other documents in a folder outside `data/`.

## Single season, or several

**Single season** (the default) — every card in here is one season. Lay the
folders out however you like.

**Several seasons** — set `seasons = "multi"` under `[build]` in `config.toml`,
and then the **first folder level under `data/` is the season name**. That is the
one and only thing this tool ever reads from a path.

```
data/
  2026-winter/          <- season "2026-winter"
    round-1/*.pdf
  2025-winter/          <- season "2025-winter"
    *.pdf
```

Name the folders so they sort sensibly — `2026-winter`, not `winter-2026` — and
give each one a readable label under `[seasons]` in `config.toml`. Each season
keeps its own ladder, and ladders are never added together; the one thing added
across seasons is each player's record, on the site's careers page.

In multi-season mode a card sitting loose in `data/`, outside any season folder,
is filed under the year on its own date, and the run says so.

## Version control

Scorecards are not committed. `.gitignore` excludes everything you put in this
folder except this note and the `.gitkeep` beside it, so a card you drop in here
cannot be published by sharing the project. That is deliberate: the cards carry
players' full names and ratings.

This tool never runs git itself. If you want to confirm the rule is in place
before your first commit:

```bash
git check-ignore -v data/example.pdf     # should print the rule that matches
```

If that prints nothing, the file would be committed, and the rule has been edited
out of `.gitignore`.

Commit the cards only if the repository is private and you specifically want the
raw scorecards archived alongside the dashboard. The comment above the rule in
`.gitignore` says which lines to remove.
