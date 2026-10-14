# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). This
is a program a club runs and not a library, so what each of the three numbers
promises about your own `config.toml`, your `data/` folder and your published
figures is spelled out in `README.md`, under *Versions and upgrading*.

## [Unreleased]

## [0.2.0] — 2026-10-15

### Fixed

- `tools/doctor.py --redact` published a card's file and folder names, which often
  name the two teams, when its path held a date, a time or a long number (five
  digits or more): a card at `…/2026-10-12/Round4-Nightjars/Nightjars-Kestrels.pdf`
  printed its source as `…/<date>/Round4-Nightjars/Nightjars-Kestrels.pdf`, because
  the date was shaped first and the path, no longer whole, matched nothing to
  withhold. It now withholds the path whole, whatever it holds, before anything else
  is shaped. A value off the card that a date or a number would part is withheld
  whole the same way, where a venue opening with its street number printed the
  number, and so are the start of a word the report's quote of a long line is cut
  inside, a word of the card a date or a time is printed against, and a name the
  header fields find part of, each of which printed part of a name; and so is the
  first line of a file that is no PDF, which could hold the teams, a file that
  begins like an HTML page, a zip, a PNG or a JPEG being named for what it is
  instead. A card in a folder the doctor may not search, or under a name too long
  for the disk, gave a traceback naming the path, with or without `--redact`, and a
  loop of symbolic links gave one under `--redact`; each is now told in a sentence.
  Under `--redact` a failure nobody foresaw is told inside the report with its
  message withheld, where it went to the terminal whole and the report was lost. If
  you have posted a redacted report in public for a card whose path held a date, a
  time or a long number, look at it again. README section: *Reporting a problem*.
- A card is filed under the night its competition line names (`Tuesday Night
  Fixtures 2026`) before the weekday of its date. Filed by its date alone, a card
  dated off its night made a competition of its own, and a card printing a range of
  days in place of a date (`Tue 29/09 - Sun 04/10`) read as undated and was filed by
  the Sunday the range closes on: on a club's 201 cards 0.1.0 built 17 competitions
  where there are 10, and one ladder the strays left said a round "accounts for none
  of" two teams and credited no bye. The competition line is the line printing
  `Night Fixtures` nearest the set table, the team names apart, so the night is not
  read off a venue or a letterhead above it that prints those words. A card dated
  off its night is now shown on the date the round's other cards dated on that night
  agree on, or on its own when they do not, with one note per card saying which and
  why; where a declared draw dates the round otherwise, `date-mismatch` quotes the
  date the card prints beside the one it is shown on. A range is dated the day of it
  on the night, in the year the competition line prints, and is undated, with a
  warning saying why, when the line prints no single year, the days are no such week
  in it, or the range is longer than a week. A line naming no weekday, or more than
  one, names no night: a range under it is dated the day it opens, which files it,
  and any other card is filed as before. `tools/doctor.py`'s verdict names the night
  wherever it overrules the date (`2026-09-24 Thursday (filed by the Tuesday its
  competition line names)`), and its `date` header field finds a range of days
  printed with no year, which `--redact` withholds. README section: *A card was
  refused*.
- A card level on games and won on its finished sets agrees with itself where its
  last rubber stopped with no box ticked. A club whose play stops at a fixed hour
  leaves that rubber unfinished whatever the score, and the card's own cross-checks
  credited it to whoever led, so rows 4-6, 2-6, 6-2 and 4-2 with `LOSES TO` and the
  away tick were told they give `games 16-16 (level) and at most 2-2 sets (level)`:
  F6 on the verb, F6 on the tick and exit 1, though the ladder scored the match
  rightly, away on sets, 3.6 to 5.6. Two cards of one club's real season were so, 4
  of the 18 errors of its build. A verb or tick naming a side is now also held to the
  rows as the ladder credits them, under each set rule every row could have been
  printed under: a finished set to its winner, a forfeited rubber to the side that
  stayed and a stopped one to nobody. That card builds with exit 0, its
  `set-unfinished` warning its one finding, and so do its mirror and a night level
  at 17-17 and won 2-1 on sets. `DEFEATS` and the home tick over the same rows still
  disagree, as do those rows read with their games columns swapped, and `LOSES TO`
  over 6-2, 6-3, 2-4 and 0-0, which only fast4 gives the away side and which fast4
  cannot have printed. A card short of a result whose finished sets bear its verb
  out is `verb-no-result` alone: 6-4, 0-5, 0-5, 0-5 with `DEFEATS` and the home tick
  was that, F6 on the verb and F6 on the tick. On a whole-match forfeit the home tick
  alone over 6-4, 4-5, 4-5, 0-2 under the away side's box agrees, where it was F6. No
  set rule backs the side a Match Forfeited box says forfeited, so under the home
  side's box the same tick is still F6, and so is the home tick over 6-4, 3-5, 2-2,
  0-0 under that box, held to the box.
  `tools/demo.py` writes such a night as a seventh special, its last rubber stopped
  with no box ticked and its games level where the format allows: in the default
  demo it is round 2's Wombats v Bunyips, so the Bunyips go top on 29.1, where the
  Wombats led on 27.0, and the build warns 7 times where it warned 6. The cost,
  measured and accepted: the reader's own check that the two games columns were not
  swapped passes more swaps, and a Match Winner tick for the side that lost, beside
  a verb for the side that won, passes it more often. Of 228481 seeded decided
  cards, each passed the reader in 13740 before and passes in 25904 now. The rule
  and order each card was decided by give none of those swaps to the verb's side,
  and that is what `verb-mismatch` and `verb-no-result` hold a verb to; but nothing
  holds the tick to them, so a card printing `DEFEATS` and ticking away over 4-6,
  3-6, 6-0, 5-4, home on games 18-16 and scored so, was F6 on the tick and is now
  reported by nothing.
- The reader's TOTAL row cross-check says what it found. It holds the printed games
  to the set rows' sums and the printed sets only to the most the rows allow, and
  said both `match the 4 set rows`, so rows of 6-1 four times under a TOTAL of sets
  2-0 read `printed games 24-4 and sets 2-0 match the 4 set rows` in
  `tools/doctor.py`, while the build failed the card on `totals-mismatch`. It reads
  `printed games 24-4 match the 4 set rows, and sets 2-0 are within the 4-0 they
  allow` now, and so does the README's doctor sample. The check is unchanged:
  `totals-mismatch` holds the printed sets to the club's own set rule.
- `tools/doctor.py`'s verdict on a card a cross-check disagrees with no longer says
  the build refuses to score it, or that something was misread. It said `build.py
  reports this as an error rather than scoring it`, where the build fails the run and
  still records the match as its set rows or its `Match Forfeited` box give it, as it
  records any other: a card printing `LOSES TO` and the away tick over rows the home
  side won three sets to one, read exactly, was told so and published played, home,
  6.2 to 3.2. It now says the readings differ because something was misread or
  misprinted, or the card counts its rows by a rule the cross-check does not; that
  `build.py` reports this as an error, which fails the run; and that the match is
  still recorded as its set rows give it under the club's rules, or as its
  `Match Forfeited` box gives it if one is ticked. It does not say the match is
  scored, which a card whose rows give no result, four sets of 0-0, is not. The
  README's paragraph on the cross-checks says the same.
- `tools/doctor.py` finds the court a card prints. It looked for a number after the
  `Court:` label, where Match Centre's cards print the court after the venue on the
  line above it, so it said `court not found` on 201 of 201 real cards while the
  reader read a court from every one; and on a card whose comments say `Court 3 was
  under water` it found court 3 beside a reader that found none, or found 12. It now
  reports the court the reader reads, and under `--redact` keeps the number.
- Each tick line in `tools/doctor.py`'s checkbox section names the box the reader
  reads it from, with its row and side: `the away Match Winner box`, `row 4's home
  Retired box`. It named the words to the right of the fill, so a tick in the away
  side's Match Winner box, whose label is to its left, read `(no label to its right)`
  (79 of 201 real cards), and a tick in a set's Forfeit or Retired box read a run of
  its neighbours' labels, `Forfeit Retired Incomplete` (8 lines on 5 real cards). A
  tick the reader takes for no box, a square no label names, or a box in no set row
  is said to be one. Under `--redact` the tick lines are kept, as the rest of the
  census is.
- A dashboard left behind by a card that an earlier build filed under the wrong
  night, and this run filed under its own, was given the reason a deleted season
  gets, "no card under data belongs to it any more", though this run had just
  read that card. Its reason under *left behind* now names the card, or counts
  the cards and names up to three fixtures, says which competition they were
  filed under and, where `--prune` would remove the stale copy, says so. While
  `config.toml` still has the section an earlier first run wrote for that
  night, the reason also says the section is all that keeps the night on the
  front page, with no results, and to delete it. It says this only where this
  run read a card of the same fixture and match id for every card the copy
  holds, under the one competition that fields both teams. A copy only some of
  whose cards were read keeps the reason it had, and so does one whose two
  teams another night fields by the same names.
- A rubber with no games on it and no box ticked reads `No score` in the match
  card's Status column, where it read `Unfinished`, a word that says the set was
  started. The report has called such a row a rubber nobody played since
  `set-no-games`, and the page now says what the report says, on every match card
  and in the season's table of every rubber and its CSV. A set that stopped with
  games on it still reads `Unfinished`, and a row whose Forfeit, Retired or
  Incomplete box is ticked still reads that box, games or none. The word is the
  row's alone, so a night called off or given up is still said once, by the card's
  own summary line, and not again on each of its rows.
- A set nobody won reads its match card's word in a player's Every set played list,
  `Unfinished`, `No score`, `Retired` or `Incomplete`, where a set with no box
  ticked printed the lowercase `played` the export stores, beside a 5-3 and a 0-0
  alike, and a retired or incomplete one its word in lowercase.
- A set a Forfeit tick decided reads `Won, forfeit` or `Lost, forfeit` in a player's
  Every set played list, where a bare `Won` or `Lost` read as a set won or lost on
  court.
- A player off a card's roster block is one warning, naming every set row they are
  on (`R1 brindle-rosellas v brindle-kookaburras d1 and s2: Priya Halloran is
  registered to brindle-magpies ...`), where it was one warning for each row that
  said the same thing. The same goes for `unknown-player`, `fill-in`,
  `fill-in-other-competition`, `player-no-team` and `off-roster`. A name on one row
  reads as before. The demo's one fill-in, on two rows of one card, is one warning
  where it was two.
- `ratings-missing` names only a player on their side's roster block, since that is
  the one place a rating is printed, and says what the block gave: `the roster
  block gives no rating for jo-calder, who played, so jo-calder shows none for this
  round.` Several names are one sentence, told of the block they are on, or of
  both blocks where they are on both. A fill-in or a stranger on no line of it is
  no longer told of a missing rating as well as being named as a fill-in or a
  stranger. One of the demo's warnings was that, for its fill-in, so with the
  change above the demo reports 4 warnings, down from 6.
- The Fill-in note on a match card and on a player's page says `is on no team’s
  roster` of a player no squad of the club registers, in the words the Team share
  tile and the players-to-check list already used, where it said they `played
  outside their registered roster`. A player another competition of the club
  registers keeps that sentence, which is true of them, and each player in
  `season.json` has a new `registered_elsewhere`, naming that competition, or
  null. A player who plays for their own team on a card whose roster block leaves
  them out is no longer a fill-in: no Fill-in note, and their games that night are
  in their own Share, where the team's caption counted them as played by somebody
  with no row in the table.
- A round the season has not reached is no longer told as a round short of cards.
  With every team's byes typed in from the association's sheet ahead of the
  season, each round still to come was told it "accounts for none of" four teams,
  missing scorecards, so find the cards: 6 notes over 3 divisions of one club's
  season. A round with no card of its own and none of a later round is still to
  come, by the rule `card-missing` asks, and its byes wait for its cards; a round
  inside the span played keeps its note. As in 0.1.0, nothing is credited for a
  round still to come, under `unplayed = "average"` too.
- With the team list unconfirmed, a round short of cards names the teams it leaves
  off, and talks of a rest only where it leaves off one. It offered to credit "a
  team resting" on a round one card short, which leaves two teams off, where
  confirming the list credits nobody: two teams off is missing scorecards, and is
  now told as that. A final is no longer told as short of cards, list confirmed or
  not, and the fullest round a round is measured against is never a final.
- A round's gap that a card filed under another competition fits now says what was
  read and where it went. A card of two of a competition's teams for one of its
  rounds, dated another weekday or printing another division, counts where it is
  filed, and the round it is missing from asked for the card, as did `card-missing`,
  which also said to run `tools/doctor.py` on it. Both now name the competition the
  card was filed under and why, and the round's note gives the file it was read
  from. Where the card leaves one team of the round on no card, the note offers
  that team's line in the byes table, after the team list is confirmed if it is
  not, since a byes table alone closes the list to the teams it names. The card
  still counts only where it is filed.
- With the team list confirmed, a declared round that has cards and leaves two or
  more teams on no fixture, card or rest now names them and asks for their cards or
  the fixtures it leaves out. A round typed in as its cards show it lists no fixture
  of a card that never came, and was told nothing but the ladder's count of matches
  played.
- A rubber that stopped with no box ticked on its row, `set-unfinished`,
  `set-tied` or `set-no-games`, was a warning of its own, so a club whose play
  stops at a fixed time whatever the score had one most nights. Each of the three
  is now one warning for the competition, listing every such rubber, with its
  score where it has games, in the order the matches were played and naming the
  cards, as in `3 rubbers stopped short of a finished set under first-to-6 (first
  to 6) and are not marked forfeit, retired or incomplete, so nobody is credited
  them: R1 ... d2 4-3, ...`. A competition with only one reads as before. They are
  still warnings, as a misread row reads the same way, and a match they leave
  short of a result is still its own `no-result`.
- The note a first run writes over `[team_names]` in `config.toml` names the two
  guesses most worth checking. It said a line behind a `#` was the program's
  best guess and to correct it if it read wrongly, and named no guess that
  might. Two are read wrongly by design, because no rule tells them apart from
  names read right: an acronym of four letters or more is read as a word, so
  `RIVERSIDE ABCD` is offered as `Riverside Abcd`, and a letter A after a word
  that names one can be read as the word "a", so `RIVERSIDE VITAMIN A ROCKETS`
  is offered as `Riverside Vitamin a Rockets`. Offered beside names that are
  right, both were uncommented with them, as written, by a club reviewing its
  file. The note now names the two, with an example of each, and so do
  `config.example.toml` and the README, which also says that the same cut keeps
  a shouted word of three letters, such as `OLD`, in capitals. How a shouted
  name is put in title case has not changed.
- The comment on the team name rule's abbreviation check named a four-letter
  acronym, beside `RTC`, as a word it keeps, where the check keeps three letters
  or fewer and title-cases four. It now names `RTC` and `TC`.
- A player's Matches tile says how many of their matches were for their own team.
  It gave every night the player was on court, a night lent to another side
  included, over the nights their own team played. In the demo Priya Halloran, who
  played three of the Brindle Magpies' four nights and one for the Brindle Rosellas,
  read `Matches 4` over `of 4 the team has played`, and on a season of five rounds a
  player lent to another side on their own side's bye read `5` over `of 4`. A name
  on no team's roster read its own count over itself, `1` over `of 1 the team has
  played`. `season.json` now gives each player a `team_matches`, how many of their
  team's nights they played, and Priya's tile reads `4` over `3 of 4 the team has
  played, 1 for another side`. A player who played only for their own team reads
  `of 4 the team has played` as before, and a name on no roster reads `on no team’s
  roster`, as its Team share tile does. The Played column under Players to check
  takes the same words: `1 of 4, 1 for another side`. `available`, the nights the
  player's team played, is `null` for a name on no roster where it was the name's
  own count, and `team_matches` is `null` beside it. Both are part of format `4`: a
  page reading a document built before this release prints no line under the
  figure. The first build after upgrading rewrites each competition's `app.js`,
  `season.json` and `season-data.js`, and the next build writes nothing.
- The overview and the Players view count as registered only the players a roster
  block registers. They counted every row of the players table, which has one for
  every name a counted set row prints, so on a season whose roster blocks register
  12 and whose set rows name one more, the overview read `13 registered players` and
  `Players used 10 of 13`, and the Players view `13 registered players.` They read
  `12 registered players · 1 on no team’s roster`, `9 of 12` over `3 yet to play · 1
  more on no team’s roster`, and `12 registered players and 1 on no team’s roster.`,
  and the view's subtitle says it lists everyone on no team's roster who played. A
  competition whose every name is on a roster reads as it did.
- A team's "Points earned by round" no longer says a round nobody played had
  nothing on its card. A night called off part way keeps the games played before
  the rain, and the line read "R5 was not played, so there is nothing on its card
  to plot." beside a Values table printing 12–5 for it. It now says what the match
  card itself says, "so nothing on its card is counted", true of a card part
  played and of a blank one alike, and then what the round was worth, as before.
- The Matches page heads a round whose every card was called off, or left blank,
  **not played** in a grey pill, where it said **played** in green over cards that
  each said they counted for nothing. A round like that with a card still missing
  from a typed-in draw says **card missing** in amber instead, since the missing
  card may be the night somebody played. A final of either kind is no longer
  named under "Finals played". The round still counts among the rounds played, as
  the association's sheet counts it, and `season.json`'s rounds carry a new
  `cards_played` count; a `season.json` built before it reads as it did.
- The overview's *Tightest match* says when the night it names was level on games
  and won on sets. Under a scheme decided on games and then sets, such a night's
  games margin is 0, and the tile read `0 games` over the two sides and the round,
  with nothing saying that one of them won it. Its foot now ends `level on games,
  decided on sets`, the words the night's own line in the Matches view uses, now
  written once and asked by both; a match decided on games reads as before.
- A chart with nothing to draw says so, where it drew its floor. The head-to-head
  key read `0 games` to `1 games` under a grid with no game in it, every cell a
  dot or a walkover's 0–0, the 1 being the floor the colours are divided by and
  not a game; the key now tops out at the grid's own largest figure and is left
  out where that is 0. *Games for and against*, *Strength by set slot* and a
  player's *Singles and doubles* drew a bar of 0 for every team, slot or
  discipline on an axis reading 1, 0, 1 when none had a game to show: after a
  round washed out, a walkover or a grand final alone, and for a rostered player
  who never played. Each now shows its empty text, worded for what it counts: `No
  regular-season games counted yet.`, `No regular-season set played yet.` and
  `No games won or lost yet.` *Points earned by round* and *Games differential by
  opponent* still draw a 0, which on those charts can be a result.
- A count of one game reads `1 game`. The *Biggest margin* tile, a head-to-head
  cell's hover, the spoken labels of the bars in *Games for and against*,
  *Strength by set slot* and *Singles and doubles*, the player page's *Team share*
  tile (`0.5 of 1 team games won`) and both of the team page's Share caption
  sentences printed `1 games`, the first of them `1 of the 12 games the team won
  belong to no row above`. Each now words its noun, and that verb, for its count,
  as the *Tightest match* tile already did.
- A ladder with nothing counted has not started, and places nobody. With round 1
  washed out, or a final the only card, no regular-season match had been counted,
  and the report still called the ladder `in progress: teams have played unequal
  numbers of matches`. The teams were placed 1 to 4 by name, the overview's top
  crowned the first of them on `0.0 points from 0 matches` (with a final alone, the
  final's loser), the team pages charted round 1 at those places, and the build
  after round 2 listed the moves out of that alphabetical order, `Riverside Rockets
  4th -> 1st`. `season.json` now says `meta.ladder_state`, one of `empty`,
  `in-progress` and `complete` by one rule, and an empty ladder places nobody,
  charts no place, names no leader and moves nobody. The report says `ladder  not
  started: no regular-season match has been counted yet`, and the overview says
  the teams are listed by name. A team with nothing counted beside teams with some
  is still placed last, and `meta.ladder_complete` is kept, as the state
  `complete`.
- The front page and the overview name the same leader, or nobody. With a match
  still to come, the front page's card named no leader and the overview's top
  crowned the top row whatever the ladder was ordered on. `season.json`'s new
  `meta.leader` is the one answer, with the points average it leads on, and both
  pages read it; where it is nobody on a ladder in progress the overview says
  why, as `No leader while teams have played unequal numbers of matches` on one
  ordered on a total. Which ladders in progress have a leader is under
  *Changed*.
- A season's progress is said as a count against its length, `3 of 10 rounds
  played`, where it read `Round 3 of 10`: with cards for rounds 2, 3 and 5 that is
  a round's label, and not the one the latest card is from. The front page's card
  says so, and so does the overview's progress box, in its label and its meter's
  spoken label alike. The bar, the percentage and the report's `3 of 10 played` are
  unchanged.
- The front page counts a competition "with results" once a night has been played
  in it, finals and forfeits in and washouts out. A competition whose every card was
  washed out has a document, and was counted for it: a washed-out division beside a
  played one read `2 of 2 competitions with results`. It now reads `1 of 2`, from
  `season.json`'s new `meta.matches_played`, the count the overview's *Matches
  played* makes. The washed-out competition's card is still linked, and says no
  regular-season match has been counted yet.
- The ladder's state is said in the terms it was decided in. F10 compares the
  matches each team has counted, which under `unplayed = "average"` include the
  byes and washouts credited the team's own average, and each sentence giving its
  answer said played. Three teams with a bye a round credited read `ladder
  complete: every team has played the same number` over a `P` column of 2, 3 and
  3; four teams with a washout beside a card not yet in read `in progress: teams
  have played unequal numbers of matches` over a `P` of 1 for every team, in the
  report, in F10's warning, on the front page, in the ladder's note and at the top
  of the overview. Where every team's count is its `P` the words are as they were;
  where a round has been credited they name the count: `every team has 4 matches
  counted, byes and washouts credited their average`, and `teams have unequal
  numbers of matches counted, byes and washouts included`. One function words it
  for all five, and `season.json` carries it as `meta.ladder_detail`, a key the
  page reads only where it is there. The document format is still `4`.
- The run report's ladder prints what each average divides by. With a round
  credited, `pts` divided by `P` is not `avg`, and the page's *Counted* column
  says why where the report said nothing: the Spinners top on `18.0` points and
  `6.00` from a `P` of 1. The table prints `cnt` between `pts` and `avg` wherever
  some team's count is above its `P`, as the page shows its column, and is 78
  columns wide at the longest team name it prints, the report's width.
- Crediting a week off can change the ladder's order, and the README,
  `config.example.toml`, the `config.toml` a first run writes and the 0.1.0 entry
  said it never could. `simple`, `sets-first` and `rubbers` rank on points first,
  and the credit is in the points: three teams with the Spinners resting twice
  read Rockets, Spinners, Aces under `zero` and Spinners, Rockets, Aces under
  `average`, in `simple` and `sets-first` alike; in `rubbers` the same nights
  bring the Spinners level on points, and wins keep the Rockets first.
  `games-bonus` ranks on the average, which the credit cannot move, and there it
  moves only teams level on average. Each now says so, and the FAQ for an order
  unlike the association's names `unplayed` beside `scoring` and `sets`. And
  `config.example.toml`'s `average` option said the credited round counts as a
  match played, which `P`, the matches played, has never counted; it now says the
  round counts in that average but not in `P`. No figure or order changes.
- The overview's progress box says `6 matches played`, where it said `6 matches
  scored` beside the data health line's `7 of 7 cards scored`: a washed-out card
  is scored, in the report's sense and the chip's, and is no match played. The
  chip keeps its word.
- A season's name is offered as its pages show it, or not offered. Over cards that print
  no season, the first run's file offered `# season = "2026 Season 2"`, a name no card,
  date or folder gave, and the review list offered `season = "2026"` for the same
  competition; over cards dated 2027 the file still offered `"2026 Season 2"` beside the
  review list's `"2027"`, and deleting its `#` put `2026 Season 2` in the page's heading
  as declared. In multi-season mode the `[seasons]` table offered
  `# 2026-winter = "2026-winter"`, and adopted it renamed the season from the
  `2026 Winter` its pages showed. The file, the review list and the `[seasons]` table now
  ask one rule: the name the pages show, else the year or years the matches' dates give,
  else nothing. Where there is nothing, the section says so and writes no `season =` line,
  and the review list says to add one with the name typed in double quotes. It had offered
  `season = ""`, which added as printed declared nothing.
- Each message about a season's name says where the name shown came from. In multi-season
  mode, a season no card names shows its folder's name made readable, while the review
  list said `scorecards do not reliably print it, so the dashboard leaves it out`, the
  file's comment said the same, and the page flagged it
  `No season label was declared, so this one was worked out from the cards`. All three now
  say what the name shown was made from: the folder's name, or, where the cards sit in no
  season folder, the year on them, which multi-season mode names the season by. The page
  says to name the season under `[seasons]` or in the competition's section. A
  competition that shows no name at all is no longer counted among the values we worked
  out: `season.json` gives its season the provenance `empty`, and the page lists nothing
  for it.
- An absence is not a value we worked out. A division whose cards name nobody, such as one
  whose only card is a washout, had `players` and `captains` listed on the page as values
  worked out, with advice to correct names nobody had printed, and a division whose
  rosters mark no captain listed `captains` the same way. `season.json` now gives each
  such aspect the provenance `empty`, and the page lists neither.
- A captain the club declared reads as declared wherever the captain is printed. The
  captains' provenance was merged over the players record of each team's captain, so a
  declared captain on no counted card was left out of it: a division whose captains were
  all declared that way read captains `derived`, and the team page flagged the captain
  `The captain was worked out from a roster mark rather than declared`, telling the club
  to declare what it had. Each team's captain is now read as declared where the club named
  one and as the card's otherwise. The page's captains advice, which it shows only where
  some captains were declared and the rest read from the `(C)` their cards print, now
  says that, rather than calling the `(C)` a guess.
- The first run offers no number for the season's length. Over cards for rounds 1 to 7, it
  wrote `# rounds_total = 7` under `Remove the # to use it`, and the review list said
  `delete the # to adopt it`; a club that did as it was told declared the season over at
  round 7, the page read `Round 7 of 7` over `100% of the declared rounds`, and round 8's
  cards stopped the next run on F3. The line is now `# rounds_total =` with nothing after
  the `=`, under a comment saying to type there the number of rounds your association's
  draw gives the season, and the review list gives it a status of its own saying the same.
  Its offer to have the file written for you counts the line among those that land
  commented out, and says one with nothing after the `=` needs its value typed there too.
  Deleted alone, the `#` leaves a line the next run refuses as not valid TOML, naming the
  line, rather than a length nobody typed. The rounds played are still named, as a count.
- The date on a competition's card on the front page says what it is:
  **4 teams · 6 cards · generated 10 Sep**. A bare date beside the count of cards
  read as the night of the last card. It is the build that last changed what that
  competition's dashboard holds, the date the dashboard's own header and footer
  give, in the same word, and a rebuild with nothing new keeps it.
- A card with nothing on it, every set row 0-0, `VS` or no verb, no tick and a
  TOTAL row of noughts or none, is reported once, by its `no-result` warning.
  0.1.0 also told a night called off before anybody was entered
  `players-unrecorded`, and a night nobody entered a result for F6's warning
  that nothing on it confirms its set rows and `totals-missing`, each saying
  again in its own words that nothing was played. Those three stand down on such
  a card, each asking it the same one question, and the `no-result` of a night
  not called off now says its rows read 0-0 and, if the match was played, to
  enter its result in Match Centre and export the card again. The match's notes
  on the dashboard no longer say that a cross-check is missing either. A card
  that ticks Match Disputed over such rows is still told `disputed`, and what
  each empty rubber lacks, but no longer `verb-absent`. A card with games on it,
  a walkover, and a card whose verb, tick or TOTAL row names a result over blank
  rows keep every finding they had, and `tools/doctor.py` says which a card is.
- A walkover is scored as the forfeit it is. Match Centre exports a match one side
  gave up whole with `Match Cancelled` ticked, the side that came ticked `Match
  Winner`, and a TOTAL row of every rubber to that side as a love set: 4-0 and 24-0
  over four rubbers of `first-to-6`. 0.1.0 read every such card as a washout and
  failed the run on it, `A match called off has no winner, so the card contradicts
  itself`. A card printing exactly that, with no Match Forfeited box ticked and any
  verb it prints naming the side that came, is now a forfeit by the side that did
  not come, paid what a forfeit is worth (`forfeit_award`, below), its verb, tick
  and TOTAL row held to the walkover rather than to its rows, and its `Unspecified
  player` cells said to be a forfeit's. Rows of play its TOTAL row does not count, a
  night given up part way, are said once, as the warning `walkover-rows-played`;
  rows that, every set finished, give the absent side the match are
  `forfeit-contradicted`, as against the box. A TOTAL row that is not that award,
  or none, is a washout's, and so is `VS`, `DRAWS WITH` or a word this program does
  not know beside the tick.
- A card ticked `Match Cancelled` that also ticks a `Match Winner` box or prints a
  winning verb, over the rows of a night the rain stopped, no longer fails the run.
  Match Centre exports a washout that way, naming the side ahead when play stopped,
  and the cancelled tick takes precedence: the match is the washout it says, no
  result and no points, as before, and its one `no-result` finding is a warning,
  as it was before 0.1.0, saying what the card also claims and that the Match
  Cancelled tick decides. The README's advice to correct such a card in Match
  Centre, which a club could not act on, is gone.
- `players-unrecorded` on a match forfeited whole by its Match Forfeited box now
  ends `this match was forfeited whole, so nobody played those rubbers`, as a
  walkover's does. 0.1.0 said nothing more of a box forfeit's `Unspecified player`
  cells, and told one ticking `Match Cancelled` too that it was called off, though
  the match is scored as the forfeit.
- A side that every rubber's `Forfeit` box gives up, over rows left 0-0, is no
  longer asked for its roster, as a side the Match Forfeited box gives up is not.
- `set-player-count` no longer tells a match forfeited whole that a rubber nobody
  played is short of a name. The side that gave the match up, by the box or as a
  walkover, naming one player on a pair's 0-0 row was told that the rubber is a
  doubles set with 1 player(s) named; that row is now passed by, as
  `set-no-games` and `set-players-missing` already pass it, and a forfeit given
  up after a rubber is still held to the names on the rubbers it played. A
  played card's row left 0-0 with no box ticked is still held to its names: the
  card does not say nobody played it, and the players' figures count them.
- A pair naming one player above Match Centre's `Unspecified player` is read as a
  full side whose second place nobody filled. The reader's note `read 1 away
  player name(s) from a row its own marker '1&2' says is doubles`, word for word
  what a pair with its second cell left blank gets, is no longer among the
  match's Parse notes, nor printed twice by `tools/doctor.py`; the blank cell is
  still told. The match page follows the one name of a pair with `+ not
  recorded`, the placeholder's or the blank cell's alike, where it showed the
  name alone, as a full side reads, and so do the matches page's table of every
  rubber and its CSV.
- A played card's `Unspecified player` cells are reported once, by
  `players-unrecorded`, and no longer also by `set-player-count` and
  `set-players-missing` as names that failed to read; a pair printing it in one
  cell and leaving the other blank is still told `set-players-missing`, for the
  blank one. Its clause says, side by side, which rubbers have nobody to credit
  and which pairs name one player, half their games credited to nobody, where it
  said of every such rubber that nobody was credited. Where a rubber printing it
  was left 0-0, the clause begins `those rubbers with games have` and not `those
  rubbers have games`. Its detail names the side, `3 placeholder cell(s): away in
  rubber(s) 1, 3, 4`.
- A rubber ticked `Forfeit` against one side is scored as Match Centre's own TOTAL
  row counts it: a love set to the other side, 6-0 under `first-to-6` and 4-0
  under `fast4`, whatever games the row prints, and every rubber of a match
  forfeited whole likewise to the side that came, rows with play on them included.
  0.1.0 scored the games printed, so a card with three rubbers ticked against the
  away side, all three of which it won on court, 2-6 6-2 1-6 3-6, was 12-20 to the
  side that forfeited, failed the run on `totals-mismatch` and `verb-mismatch`
  against its own TOTAL row of 4-0 and 24-2, and paid 3.2 and 6.0 under
  `games-bonus`; it now builds clean, 24-2 and 6.4 and 2.2. Under
  `forfeit_award = "as-played"` the games printed stand and the tick still gives
  the set. A TOTAL row may total the rows as scored or as printed, and a verb or
  tick may go by either. The ladder's Games column, `games-bonus` and the players'
  games count the games scored; the match page says the games played beside them,
  as `Forfeit, 2–6 on court`, and so does a player's list of sets; a rubber left
  0-0 under a forfeit, which nobody played, is in no player's figures and is
  counted in the team page's caption; and the overview's Games played counts the
  games printed, its tiles leaving out a night not one rubber of which was played,
  and its Sets completed a rubber nobody played. A washout's rows are scored so
  too, as its TOTAL row counts them, though the night still counts for nothing.
- A set one side's `Retired` box is ticked on is the other side's, its games
  raised to win it where they were fewer: 5-1 with the away box ticked is 6-1 to
  home, 4-2 with the home box 4-6 to away, and 0-0 with the away box 6-0. 0.1.0
  gave every retired set to nobody, so a card printing Match Centre's 4-0 and 24-8
  over a retirement at 5-1 failed the run on `totals-mismatch`, and a night whose
  result needed every rubber had none. Both boxes ticked is nobody's set, at the
  games printed, and the new warning `set-retired-both`; `Incomplete` is nobody's,
  as before; under `as-played` the set is still the other side's, at the games
  printed. The demo's retired-set card prints a verb and ticks a winner where the
  retirement decides the night.
- `set-forfeit-contradicted` is no longer said of a row ticking `Forfeit` against
  the side it shows winning when the card's TOTAL row counts the row as Match
  Centre does, a love set to the other side, since the card then agrees with
  itself. Said, it now says what the row is scored: `scored 6-0 to` the side that
  stayed `as forfeited`, or `credited to` it under `as-played`.
- The first run offers no home court that no card gave. Its `courts` table wrote
  `# "Bramble Finches" = 1` for the first team by name in every competition, from a rule
  in the writer that no card prints a court, though every card prints the court it was
  played on: deleting the `#` declared court 1 for a team whose home cards print court 5.
  The table now offers the court the cards print (see Added), and keeps that sample line
  only for a competition none of whose cards prints a court.
- `--discover --force` over a config whose declared draw closes its team list wrote the
  list it offered under `# The full list of teams in this competition, as read from the
  cards.`, though the list left out a team the cards name, the one the run's F8 reports.
  The comment now says the list is as the declared draw names it, and the list with that
  team follows it behind a `#`.
- The test that nothing published names a particular club or player reads each line as
  a slug folds it, its case and accents set aside, and asks each term it is given as
  written and as its slug, so a name written with an accent its listed spelling has not,
  or written as a slug, is found; the terms it is given may be match ids. The examples of
  the reader's comment on the identity line, of the parser and doctor tests that read
  it, of the slug test and of this file's 0.1.0 entry on the identity line are a made-up
  match id, `1000100`, and a made-up name, where 0.1.0 used ones from a real club's
  cards.
- The test suite passes in the copy you build your dashboard in, with your cards in
  `data/`, your `config.toml` and the `site/` a build leaves there. Until now, in a git
  clone where `tools/demo.py` and `build.py` had run, it failed three tests: the
  README's block for a folder with no cards, because `build.py` rightly pointed at the
  cards beside it; the test that no club's config ships, which found the `config.toml`
  git ignores; and the test that every command can say its version, which found the
  demo's cards already in `data/`. A virtual environment in `.venv/` failed one more,
  and the lock file Emacs leaves beside a file it has open, a link to nothing, gave
  eighteen errors. The tests now ask git what ships, or run the program in a fresh copy
  of itself, and read a link as a link. `.venv/` is gitignored, since before Python
  3.13 a virtual environment brings no ignore file of its own and `git add .` would
  commit pip, and the test that nothing published names a particular club or player no
  longer reads a `config.toml` git ignores. A test makes a club's copy, a clone with
  the demo's cards, a build, a `.venv/` and a lock file, and runs there the tests that
  read the folder, each of which must pass without skipping. The suite's own git no
  longer takes a repository from the environment, so run from `git rebase -x` or a
  commit hook it makes no commit or tag in the repository running it and leaves its
  index and settings alone. A folder unzipped from a release, or a computer without
  git, has no git to ask, and there one test still fails once a build has written
  `config.toml`: run the suite before the first build. README section: *Checking the
  tool itself*.

### Added

- The data health detail on a dashboard can show its errors alone, its warnings
  alone or both. Above the list, where it holds both kinds, three radio buttons
  under **Show** read **Both**, the errors and the warnings, each counted in the
  summary's words, so a card the build refused is counted apart there as the
  summary counts it. A choice hides the other kind and changes no count. The page
  opens on **Both** every time and keeps no choice from one visit to the next, and
  an open list prints whole whatever was chosen.
- `[rules] forfeit_award` (`full-games`, `fixed` or `as-played`) and
  `forfeit_points`: what a match forfeited whole is worth, overridable under
  `[competitions.<id>]` and written behind a `#` by the first run. The default is
  `full-games` under every preset: a win with every rubber a love set to the side
  that turned up, and 0 to the side that gave the match up, which is 6.4 and 0
  under `games-bonus` over four rubbers of `first-to-6`, where 0.1.0 paid 4.0 and
  2.0, as played. `fixed` pays `forfeit_points` and 0, and stops the run without
  it; `forfeit_points` under another award is reported as not read; `as-played` is
  0.1.0's pricing. The Match Forfeited box, every rubber 0-0 and ticked `Forfeit`
  against one side, and a walkover are paid alike. Whenever a forfeit is not paid
  as played, the run report's scoring line and the dashboard's rules banner say
  what it is paid.
- **Every player this season, on one page.** A single-season build that prints
  players' names writes `careers.html` too, as *Players this season*, linked from
  the front page after the last competition and from every player's page (*Season
  across competitions →*). It lists everyone the cards name, by name and not as a
  ranking, with a row for each competition they appeared in, the team whose
  roster they are on there, and a total; every row opens that competition's page
  for them. So a player entered in a Monday and a Tuesday division, or a fill-in,
  has their season added up in one place. The report's `cards` section says
  `careers      2 of 11 player(s) played in more than one competition`. The page
  keeps its file name, so switching to multi-season mode keeps every link to it,
  and switching back rewrites it rather than listing it under *left behind*. A
  build that withholds names writes no page and no link, and its `careers` line
  says why in single-season words: a withheld label belongs to one competition,
  not to a person. Such a run over a site that printed names lists the page under
  *left behind*, since it still prints every name, and exits 1 until `--prune`
  removes it, as it does for a dashboard left with names. A one-file dashboard
  has no link to it. A `careers.html` of your own in `site/` now stops a
  single-season build that prints names, as it stops a multi-season one, and is
  left as it is. A `[namesakes]` line naming teams of two competitions is no
  longer called useless in single-season mode, because on this page it tells two
  people apart.
- `python3 build.py --discover` over an existing `config.toml` writes a proposal beside it,
  `config.proposed.toml`, and leaves `config.toml` as it is. It had refused, exit 2, before
  reading a card, and the one way to ask the cards what they would write was `--force`,
  which writes the file fresh and drops a declared draw and every comment. The run now
  builds the dashboard as a plain run does, with the same exit status, and the proposal is
  the file `--discover --force` would write, under a header that says which run wrote it,
  that nothing reads it, how to adopt it, and what adopting it whole loses. The review
  section opens with what it offers that `config.toml` lacks: competitions it does not
  declare, teams a declared list or draw lacks, captains (a blank one among them) and
  home courts where it names none or another, and any other value it does not hold, each
  with its lines; or it says the proposal offers nothing `config.toml` lacks. It writes
  over a proposal of its own, and leaves one that holds what it would write already as it
  is, date and all, as the site's pages are. It refuses, exit 2, a file of that name it did
  not write, or cannot read, and says which. `.gitignore` ignores `*.proposed.toml`, since
  a proposal holds every name `config.toml` does. `--config club.toml` gets
  `club.proposed.toml`. `--check --discover` writes nothing, and `--discover --force`
  writes over `config.toml` as before, with the proposal's body and so its offers.
- The first run offers each team's home court from its cards. The `courts` table writes,
  behind a `#`, the court each team's home cards print most, in the words the captains
  are offered in, with a comment naming each court and on how many cards where they print
  more than one. Two courts printed equally often are named and neither is offered, and a
  team with no home card gets no line. A court the club declared is written back live,
  with the court its home cards print below it behind a `#` where the two differ. The
  dashboard shows a home court only once it is declared.
- A pushed version tag makes the GitHub Release. Pushing a tag had run no workflow,
  and the notes were pasted by hand. `.github/workflows/release.yml` runs on a tag
  starting with `v`: `tools/release.py check` refuses in seconds, with every reason at
  once, a tag the code and the changelog do not both describe as released; the suite
  runs as `tests.yml` itself, which a workflow may now call; and `tools/release.py
  publish` zips the tagged tree as `matchcentre-dashboard-X.Y.Z.zip` and makes the
  release with `gh`. Its notes are the changelog's section, each wrapped paragraph and
  item on one line, then the zip's SHA-256 and the run that tested it; `check` refuses
  notes longer than the 125,000 characters GitHub takes. A re-run, of the same tagged
  commit, finishes a release that stopped half way and publishes one left a draft; a
  run whose tag has since moved on GitHub publishes nothing; and a version with a
  pre-release part is published as one. `tools/release.py prepare X.Y.Z` writes the
  version, moves `[Unreleased]` under a dated heading and rewrites every example of
  the report's first line in the files git keeps, which the suite had found only after
  the bump; a final after its own release candidates gathers their changes under it
  too, leaving their sections where they are, and a write that fails part way leaves
  each file whole and puts back the ones already written. `publish --dry-run` writes
  the zip and the notes into `dist/`, which git ignores, without making a release.

### Changed

- A ladder ordered on the points average, as `games-bonus`'s is, names its leader
  while teams have different numbers of matches counted. The average divides each
  team's points by its own matches counted, and under the default
  `unplayed = "zero"` a bye, a cancelled match or a washout is in neither, so the
  order already stood. The front page now says `<team> lead on <average>`, and the
  top of the overview names the same team, where the front page said the ladder was
  in progress. Such a ladder in a division of an odd number of teams, a bye every
  round, is led all season but for a tie at the top (below), where it was led only
  after the rounds the byes came out even. The note under the ladder says the order
  stands rather than calling it a snapshot, and the F10 warning still names the
  teams with fewer matches counted, in case one is a card not yet copied in, and
  says it costs them no place; a team with no match counted yet is still placed
  last, and the warning and the note say that of it. Two teams level on the average
  with different numbers counted are put in order by the next thing the ladder is
  ordered by, under `games-bonus` points, a total. There the warning says the tie
  goes to points and that having counted fewer, by one match or more, places the
  team below, and the note says that part of the order is a snapshot and names what
  the tie goes to. If the team on top is level so, nobody is named the leader, and
  the front page and the overview say it is for that tie and not for the counts.
  `season.json` says which kind of order it is as `meta.ordered_on_average`, whether
  it stands on the counts it has as `meta.order_stands`, and why a leader is
  withheld for a tie at the top as `meta.leader_withheld`; the document format is
  still `4`. A ladder ordered on points (`sets-first`, `rubbers` and `simple`) names
  nobody until the counts are level, as before, since the team on top there may be
  on top for having played more.

## [0.2.0-rc.1] — 2026-10-14

### Fixed

- `tools/doctor.py --redact` published a card's file and folder names, which often
  name the two teams, when its path held a date, a time or a long number (five
  digits or more): a card at `…/2026-10-12/Round4-Nightjars/Nightjars-Kestrels.pdf`
  printed its source as `…/<date>/Round4-Nightjars/Nightjars-Kestrels.pdf`, because
  the date was shaped first and the path, no longer whole, matched nothing to
  withhold. It now withholds the path whole, whatever it holds, before anything else
  is shaped. A value off the card that a date or a number would part is withheld
  whole the same way, where a venue opening with its street number printed the
  number, and so are the start of a word the report's quote of a long line is cut
  inside, a word of the card a date or a time is printed against, and a name the
  header fields find part of, each of which printed part of a name; and so is the
  first line of a file that is no PDF, which could hold the teams, a file that
  begins like an HTML page, a zip, a PNG or a JPEG being named for what it is
  instead. A card in a folder the doctor may not search, or under a name too long
  for the disk, gave a traceback naming the path, with or without `--redact`, and a
  loop of symbolic links gave one under `--redact`; each is now told in a sentence.
  Under `--redact` a failure nobody foresaw is told inside the report with its
  message withheld, where it went to the terminal whole and the report was lost. If
  you have posted a redacted report in public for a card whose path held a date, a
  time or a long number, look at it again. README section: *Reporting a problem*.
- A card is filed under the night its competition line names (`Tuesday Night
  Fixtures 2026`) before the weekday of its date. Filed by its date alone, a card
  dated off its night made a competition of its own, and a card printing a range of
  days in place of a date (`Tue 29/09 - Sun 04/10`) read as undated and was filed by
  the Sunday the range closes on: on a club's 201 cards 0.1.0 built 17 competitions
  where there are 10, and one ladder the strays left said a round "accounts for none
  of" two teams and credited no bye. The competition line is the line printing
  `Night Fixtures` nearest the set table, the team names apart, so the night is not
  read off a venue or a letterhead above it that prints those words. A card dated
  off its night is now shown on the date the round's other cards dated on that night
  agree on, or on its own when they do not, with one note per card saying which and
  why; where a declared draw dates the round otherwise, `date-mismatch` quotes the
  date the card prints beside the one it is shown on. A range is dated the day of it
  on the night, in the year the competition line prints, and is undated, with a
  warning saying why, when the line prints no single year, the days are no such week
  in it, or the range is longer than a week. A line naming no weekday, or more than
  one, names no night: a range under it is dated the day it opens, which files it,
  and any other card is filed as before. `tools/doctor.py`'s verdict names the night
  wherever it overrules the date (`2026-09-24 Thursday (filed by the Tuesday its
  competition line names)`), and its `date` header field finds a range of days
  printed with no year, which `--redact` withholds. README section: *A card was
  refused*.
- A card level on games and won on its finished sets agrees with itself where its
  last rubber stopped with no box ticked. A club whose play stops at a fixed hour
  leaves that rubber unfinished whatever the score, and the card's own cross-checks
  credited it to whoever led, so rows 4-6, 2-6, 6-2 and 4-2 with `LOSES TO` and the
  away tick were told they give `games 16-16 (level) and at most 2-2 sets (level)`:
  F6 on the verb, F6 on the tick and exit 1, though the ladder scored the match
  rightly, away on sets, 3.6 to 5.6. Two cards of one club's real season were so, 4
  of the 18 errors of its build. A verb or tick naming a side is now also held to the
  rows as the ladder credits them, under each set rule every row could have been
  printed under: a finished set to its winner, a forfeited rubber to the side that
  stayed and a stopped one to nobody. That card builds with exit 0, its
  `set-unfinished` warning its one finding, and so do its mirror and a night level
  at 17-17 and won 2-1 on sets. `DEFEATS` and the home tick over the same rows still
  disagree, as do those rows read with their games columns swapped, and `LOSES TO`
  over 6-2, 6-3, 2-4 and 0-0, which only fast4 gives the away side and which fast4
  cannot have printed. A card short of a result whose finished sets bear its verb
  out is `verb-no-result` alone: 6-4, 0-5, 0-5, 0-5 with `DEFEATS` and the home tick
  was that, F6 on the verb and F6 on the tick. On a whole-match forfeit the home tick
  alone over 6-4, 4-5, 4-5, 0-2 under the away side's box agrees, where it was F6. No
  set rule backs the side a Match Forfeited box says forfeited, so under the home
  side's box the same tick is still F6, and so is the home tick over 6-4, 3-5, 2-2,
  0-0 under that box, held to the box.
  `tools/demo.py` writes such a night as a seventh special, its last rubber stopped
  with no box ticked and its games level where the format allows: in the default
  demo it is round 2's Wombats v Bunyips, so the Bunyips go top on 29.1, where the
  Wombats led on 27.0, and the build warns 7 times where it warned 6. The cost,
  measured and accepted: the reader's own check that the two games columns were not
  swapped passes more swaps, and a Match Winner tick for the side that lost, beside
  a verb for the side that won, passes it more often. Of 228481 seeded decided
  cards, each passed the reader in 13740 before and passes in 25904 now. The rule
  and order each card was decided by give none of those swaps to the verb's side,
  and that is what `verb-mismatch` and `verb-no-result` hold a verb to; but nothing
  holds the tick to them, so a card printing `DEFEATS` and ticking away over 4-6,
  3-6, 6-0, 5-4, home on games 18-16 and scored so, was F6 on the tick and is now
  reported by nothing.
- The reader's TOTAL row cross-check says what it found. It holds the printed games
  to the set rows' sums and the printed sets only to the most the rows allow, and
  said both `match the 4 set rows`, so rows of 6-1 four times under a TOTAL of sets
  2-0 read `printed games 24-4 and sets 2-0 match the 4 set rows` in
  `tools/doctor.py`, while the build failed the card on `totals-mismatch`. It reads
  `printed games 24-4 match the 4 set rows, and sets 2-0 are within the 4-0 they
  allow` now, and so does the README's doctor sample. The check is unchanged:
  `totals-mismatch` holds the printed sets to the club's own set rule.
- `tools/doctor.py`'s verdict on a card a cross-check disagrees with no longer says
  the build refuses to score it, or that something was misread. It said `build.py
  reports this as an error rather than scoring it`, where the build fails the run and
  still records the match as its set rows or its `Match Forfeited` box give it, as it
  records any other: a card printing `LOSES TO` and the away tick over rows the home
  side won three sets to one, read exactly, was told so and published played, home,
  6.2 to 3.2. It now says the readings differ because something was misread or
  misprinted, or the card counts its rows by a rule the cross-check does not; that
  `build.py` reports this as an error, which fails the run; and that the match is
  still recorded as its set rows give it under the club's rules, or as its
  `Match Forfeited` box gives it if one is ticked. It does not say the match is
  scored, which a card whose rows give no result, four sets of 0-0, is not. The
  README's paragraph on the cross-checks says the same.
- `tools/doctor.py` finds the court a card prints. It looked for a number after the
  `Court:` label, where Match Centre's cards print the court after the venue on the
  line above it, so it said `court not found` on 201 of 201 real cards while the
  reader read a court from every one; and on a card whose comments say `Court 3 was
  under water` it found court 3 beside a reader that found none, or found 12. It now
  reports the court the reader reads, and under `--redact` keeps the number.
- Each tick line in `tools/doctor.py`'s checkbox section names the box the reader
  reads it from, with its row and side: `the away Match Winner box`, `row 4's home
  Retired box`. It named the words to the right of the fill, so a tick in the away
  side's Match Winner box, whose label is to its left, read `(no label to its right)`
  (79 of 201 real cards), and a tick in a set's Forfeit or Retired box read a run of
  its neighbours' labels, `Forfeit Retired Incomplete` (8 lines on 5 real cards). A
  tick the reader takes for no box, a square no label names, or a box in no set row
  is said to be one. Under `--redact` the tick lines are kept, as the rest of the
  census is.
- A dashboard left behind by a card that an earlier build filed under the wrong
  night, and this run filed under its own, was given the reason a deleted season
  gets, "no card under data belongs to it any more", though this run had just
  read that card. Its reason under *left behind* now names the card, or counts
  the cards and names up to three fixtures, says which competition they were
  filed under and, where `--prune` would remove the stale copy, says so. While
  `config.toml` still has the section an earlier first run wrote for that
  night, the reason also says the section is all that keeps the night on the
  front page, with no results, and to delete it. It says this only where this
  run read a card of the same fixture and match id for every card the copy
  holds, under the one competition that fields both teams. A copy only some of
  whose cards were read keeps the reason it had, and so does one whose two
  teams another night fields by the same names.
- A rubber with no games on it and no box ticked reads `No score` in the match
  card's Status column, where it read `Unfinished`, a word that says the set was
  started. The report has called such a row a rubber nobody played since
  `set-no-games`, and the page now says what the report says, on every match card
  and in the season's table of every rubber and its CSV. A set that stopped with
  games on it still reads `Unfinished`, and a row whose Forfeit, Retired or
  Incomplete box is ticked still reads that box, games or none. The word is the
  row's alone, so a night called off or given up is still said once, by the card's
  own summary line, and not again on each of its rows.
- A set nobody won reads its match card's word in a player's Every set played list,
  `Unfinished`, `No score`, `Retired` or `Incomplete`, where a set with no box
  ticked printed the lowercase `played` the export stores, beside a 5-3 and a 0-0
  alike, and a retired or incomplete one its word in lowercase.
- A set a Forfeit tick decided reads `Won, forfeit` or `Lost, forfeit` in a player's
  Every set played list, where a bare `Won` or `Lost` read as a set won or lost on
  court.
- A player off a card's roster block is one warning, naming every set row they are
  on (`R1 brindle-rosellas v brindle-kookaburras d1 and s2: Priya Halloran is
  registered to brindle-magpies ...`), where it was one warning for each row that
  said the same thing. The same goes for `unknown-player`, `fill-in`,
  `fill-in-other-competition`, `player-no-team` and `off-roster`. A name on one row
  reads as before. The demo's one fill-in, on two rows of one card, is one warning
  where it was two.
- `ratings-missing` names only a player on their side's roster block, since that is
  the one place a rating is printed, and says what the block gave: `the roster
  block gives no rating for jo-calder, who played, so jo-calder shows none for this
  round.` Several names are one sentence, told of the block they are on, or of
  both blocks where they are on both. A fill-in or a stranger on no line of it is
  no longer told of a missing rating as well as being named as a fill-in or a
  stranger. One of the demo's warnings was that, for its fill-in, so with the
  change above the demo reports 4 warnings, down from 6.
- The Fill-in note on a match card and on a player's page says `is on no team’s
  roster` of a player no squad of the club registers, in the words the Team share
  tile and the players-to-check list already used, where it said they `played
  outside their registered roster`. A player another competition of the club
  registers keeps that sentence, which is true of them, and each player in
  `season.json` has a new `registered_elsewhere`, naming that competition, or
  null. A player who plays for their own team on a card whose roster block leaves
  them out is no longer a fill-in: no Fill-in note, and their games that night are
  in their own Share, where the team's caption counted them as played by somebody
  with no row in the table.
- A round the season has not reached is no longer told as a round short of cards.
  With every team's byes typed in from the association's sheet ahead of the
  season, each round still to come was told it "accounts for none of" four teams,
  missing scorecards, so find the cards: 6 notes over 3 divisions of one club's
  season. A round with no card of its own and none of a later round is still to
  come, by the rule `card-missing` asks, and its byes wait for its cards; a round
  inside the span played keeps its note. As in 0.1.0, nothing is credited for a
  round still to come, under `unplayed = "average"` too.
- With the team list unconfirmed, a round short of cards names the teams it leaves
  off, and talks of a rest only where it leaves off one. It offered to credit "a
  team resting" on a round one card short, which leaves two teams off, where
  confirming the list credits nobody: two teams off is missing scorecards, and is
  now told as that. A final is no longer told as short of cards, list confirmed or
  not, and the fullest round a round is measured against is never a final.
- A round's gap that a card filed under another competition fits now says what was
  read and where it went. A card of two of a competition's teams for one of its
  rounds, dated another weekday or printing another division, counts where it is
  filed, and the round it is missing from asked for the card, as did `card-missing`,
  which also said to run `tools/doctor.py` on it. Both now name the competition the
  card was filed under and why, and the round's note gives the file it was read
  from. Where the card leaves one team of the round on no card, the note offers
  that team's line in the byes table, after the team list is confirmed if it is
  not, since a byes table alone closes the list to the teams it names. The card
  still counts only where it is filed.
- With the team list confirmed, a declared round that has cards and leaves two or
  more teams on no fixture, card or rest now names them and asks for their cards or
  the fixtures it leaves out. A round typed in as its cards show it lists no fixture
  of a card that never came, and was told nothing but the ladder's count of matches
  played.
- A rubber that stopped with no box ticked on its row, `set-unfinished`,
  `set-tied` or `set-no-games`, was a warning of its own, so a club whose play
  stops at a fixed time whatever the score had one most nights. Each of the three
  is now one warning for the competition, listing every such rubber, with its
  score where it has games, in the order the matches were played and naming the
  cards, as in `3 rubbers stopped short of a finished set under first-to-6 (first
  to 6) and are not marked forfeit, retired or incomplete, so nobody is credited
  them: R1 ... d2 4-3, ...`. A competition with only one reads as before. They are
  still warnings, as a misread row reads the same way, and a match they leave
  short of a result is still its own `no-result`.
- The note a first run writes over `[team_names]` in `config.toml` names the two
  guesses most worth checking. It said a line behind a `#` was the program's
  best guess and to correct it if it read wrongly, and named no guess that
  might. Two are read wrongly by design, because no rule tells them apart from
  names read right: an acronym of four letters or more is read as a word, so
  `RIVERSIDE ABCD` is offered as `Riverside Abcd`, and a letter A after a word
  that names one can be read as the word "a", so `RIVERSIDE VITAMIN A ROCKETS`
  is offered as `Riverside Vitamin a Rockets`. Offered beside names that are
  right, both were uncommented with them, as written, by a club reviewing its
  file. The note now names the two, with an example of each, and so do
  `config.example.toml` and the README, which also says that the same cut keeps
  a shouted word of three letters, such as `OLD`, in capitals. How a shouted
  name is put in title case has not changed.
- The comment on the team name rule's abbreviation check named a four-letter
  acronym, beside `RTC`, as a word it keeps, where the check keeps three letters
  or fewer and title-cases four. It now names `RTC` and `TC`.
- A player's Matches tile says how many of their matches were for their own team.
  It gave every night the player was on court, a night lent to another side
  included, over the nights their own team played. In the demo Priya Halloran, who
  played three of the Brindle Magpies' four nights and one for the Brindle Rosellas,
  read `Matches 4` over `of 4 the team has played`, and on a season of five rounds a
  player lent to another side on their own side's bye read `5` over `of 4`. A name
  on no team's roster read its own count over itself, `1` over `of 1 the team has
  played`. `season.json` now gives each player a `team_matches`, how many of their
  team's nights they played, and Priya's tile reads `4` over `3 of 4 the team has
  played, 1 for another side`. A player who played only for their own team reads
  `of 4 the team has played` as before, and a name on no roster reads `on no team’s
  roster`, as its Team share tile does. The Played column under Players to check
  takes the same words: `1 of 4, 1 for another side`. `available`, the nights the
  player's team played, is `null` for a name on no roster where it was the name's
  own count, and `team_matches` is `null` beside it. Both are part of format `4`: a
  page reading a document built before this release prints no line under the
  figure. The first build after upgrading rewrites each competition's `app.js`,
  `season.json` and `season-data.js`, and the next build writes nothing.
- The overview and the Players view count as registered only the players a roster
  block registers. They counted every row of the players table, which has one for
  every name a counted set row prints, so on a season whose roster blocks register
  12 and whose set rows name one more, the overview read `13 registered players` and
  `Players used 10 of 13`, and the Players view `13 registered players.` They read
  `12 registered players · 1 on no team’s roster`, `9 of 12` over `3 yet to play · 1
  more on no team’s roster`, and `12 registered players and 1 on no team’s roster.`,
  and the view's subtitle says it lists everyone on no team's roster who played. A
  competition whose every name is on a roster reads as it did.
- A team's "Points earned by round" no longer says a round nobody played had
  nothing on its card. A night called off part way keeps the games played before
  the rain, and the line read "R5 was not played, so there is nothing on its card
  to plot." beside a Values table printing 12–5 for it. It now says what the match
  card itself says, "so nothing on its card is counted", true of a card part
  played and of a blank one alike, and then what the round was worth, as before.
- The Matches page heads a round whose every card was called off, or left blank,
  **not played** in a grey pill, where it said **played** in green over cards that
  each said they counted for nothing. A round like that with a card still missing
  from a typed-in draw says **card missing** in amber instead, since the missing
  card may be the night somebody played. A final of either kind is no longer
  named under "Finals played". The round still counts among the rounds played, as
  the association's sheet counts it, and `season.json`'s rounds carry a new
  `cards_played` count; a `season.json` built before it reads as it did.
- The overview's *Tightest match* says when the night it names was level on games
  and won on sets. Under a scheme decided on games and then sets, such a night's
  games margin is 0, and the tile read `0 games` over the two sides and the round,
  with nothing saying that one of them won it. Its foot now ends `level on games,
  decided on sets`, the words the night's own line in the Matches view uses, now
  written once and asked by both; a match decided on games reads as before.
- A chart with nothing to draw says so, where it drew its floor. The head-to-head
  key read `0 games` to `1 games` under a grid with no game in it, every cell a
  dot or a walkover's 0–0, the 1 being the floor the colours are divided by and
  not a game; the key now tops out at the grid's own largest figure and is left
  out where that is 0. *Games for and against*, *Strength by set slot* and a
  player's *Singles and doubles* drew a bar of 0 for every team, slot or
  discipline on an axis reading 1, 0, 1 when none had a game to show: after a
  round washed out, a walkover or a grand final alone, and for a rostered player
  who never played. Each now shows its empty text, worded for what it counts: `No
  regular-season games counted yet.`, `No regular-season set played yet.` and
  `No games won or lost yet.` *Points earned by round* and *Games differential by
  opponent* still draw a 0, which on those charts can be a result.
- A count of one game reads `1 game`. The *Biggest margin* tile, a head-to-head
  cell's hover, the spoken labels of the bars in *Games for and against*,
  *Strength by set slot* and *Singles and doubles*, the player page's *Team share*
  tile (`0.5 of 1 team games won`) and both of the team page's Share caption
  sentences printed `1 games`, the first of them `1 of the 12 games the team won
  belong to no row above`. Each now words its noun, and that verb, for its count,
  as the *Tightest match* tile already did.
- A ladder with nothing counted has not started, and places nobody. With round 1
  washed out, or a final the only card, no regular-season match had been counted,
  and the report still called the ladder `in progress: teams have played unequal
  numbers of matches`. The teams were placed 1 to 4 by name, the overview's top
  crowned the first of them on `0.0 points from 0 matches` (with a final alone, the
  final's loser), the team pages charted round 1 at those places, and the build
  after round 2 listed the moves out of that alphabetical order, `Riverside Rockets
  4th -> 1st`. `season.json` now says `meta.ladder_state`, one of `empty`,
  `in-progress` and `complete` by one rule, and an empty ladder places nobody,
  charts no place, names no leader and moves nobody. The report says `ladder  not
  started: no regular-season match has been counted yet`, and the overview says
  the teams are listed by name. A team with nothing counted beside teams with some
  is still placed last, and `meta.ladder_complete` is kept, as the state
  `complete`.
- The front page and the overview name the same leader, or nobody. With a match
  still to come, the front page's card named no leader and the overview's top
  crowned the top row whatever the ladder was ordered on. `season.json`'s new
  `meta.leader` is the one answer, with the points average it leads on, and both
  pages read it; where it is nobody on a ladder in progress the overview says
  why, as `No leader while teams have played unequal numbers of matches` on one
  ordered on a total. Which ladders in progress have a leader is under
  *Changed*.
- A season's progress is said as a count against its length, `3 of 10 rounds
  played`, where it read `Round 3 of 10`: with cards for rounds 2, 3 and 5 that is
  a round's label, and not the one the latest card is from. The front page's card
  says so, and so does the overview's progress box, in its label and its meter's
  spoken label alike. The bar, the percentage and the report's `3 of 10 played` are
  unchanged.
- The front page counts a competition "with results" once a night has been played
  in it, finals and forfeits in and washouts out. A competition whose every card was
  washed out has a document, and was counted for it: a washed-out division beside a
  played one read `2 of 2 competitions with results`. It now reads `1 of 2`, from
  `season.json`'s new `meta.matches_played`, the count the overview's *Matches
  played* makes. The washed-out competition's card is still linked, and says no
  regular-season match has been counted yet.
- The ladder's state is said in the terms it was decided in. F10 compares the
  matches each team has counted, which under `unplayed = "average"` include the
  byes and washouts credited the team's own average, and each sentence giving its
  answer said played. Three teams with a bye a round credited read `ladder
  complete: every team has played the same number` over a `P` column of 2, 3 and
  3; four teams with a washout beside a card not yet in read `in progress: teams
  have played unequal numbers of matches` over a `P` of 1 for every team, in the
  report, in F10's warning, on the front page, in the ladder's note and at the top
  of the overview. Where every team's count is its `P` the words are as they were;
  where a round has been credited they name the count: `every team has 4 matches
  counted, byes and washouts credited their average`, and `teams have unequal
  numbers of matches counted, byes and washouts included`. One function words it
  for all five, and `season.json` carries it as `meta.ladder_detail`, a key the
  page reads only where it is there. The document format is still `4`.
- The run report's ladder prints what each average divides by. With a round
  credited, `pts` divided by `P` is not `avg`, and the page's *Counted* column
  says why where the report said nothing: the Spinners top on `18.0` points and
  `6.00` from a `P` of 1. The table prints `cnt` between `pts` and `avg` wherever
  some team's count is above its `P`, as the page shows its column, and is 78
  columns wide at the longest team name it prints, the report's width.
- Crediting a week off can change the ladder's order, and the README,
  `config.example.toml`, the `config.toml` a first run writes and the 0.1.0 entry
  said it never could. `simple`, `sets-first` and `rubbers` rank on points first,
  and the credit is in the points: three teams with the Spinners resting twice
  read Rockets, Spinners, Aces under `zero` and Spinners, Rockets, Aces under
  `average`, in `simple` and `sets-first` alike; in `rubbers` the same nights
  bring the Spinners level on points, and wins keep the Rockets first.
  `games-bonus` ranks on the average, which the credit cannot move, and there it
  moves only teams level on average. Each now says so, and the FAQ for an order
  unlike the association's names `unplayed` beside `scoring` and `sets`. And
  `config.example.toml`'s `average` option said the credited round counts as a
  match played, which `P`, the matches played, has never counted; it now says the
  round counts in that average but not in `P`. No figure or order changes.
- The overview's progress box says `6 matches played`, where it said `6 matches
  scored` beside the data health line's `7 of 7 cards scored`: a washed-out card
  is scored, in the report's sense and the chip's, and is no match played. The
  chip keeps its word.
- A season's name is offered as its pages show it, or not offered. Over cards that print
  no season, the first run's file offered `# season = "2026 Season 2"`, a name no card,
  date or folder gave, and the review list offered `season = "2026"` for the same
  competition; over cards dated 2027 the file still offered `"2026 Season 2"` beside the
  review list's `"2027"`, and deleting its `#` put `2026 Season 2` in the page's heading
  as declared. In multi-season mode the `[seasons]` table offered
  `# 2026-winter = "2026-winter"`, and adopted it renamed the season from the
  `2026 Winter` its pages showed. The file, the review list and the `[seasons]` table now
  ask one rule: the name the pages show, else the year or years the matches' dates give,
  else nothing. Where there is nothing, the section says so and writes no `season =` line,
  and the review list says to add one with the name typed in double quotes. It had offered
  `season = ""`, which added as printed declared nothing.
- Each message about a season's name says where the name shown came from. In multi-season
  mode, a season no card names shows its folder's name made readable, while the review
  list said `scorecards do not reliably print it, so the dashboard leaves it out`, the
  file's comment said the same, and the page flagged it
  `No season label was declared, so this one was worked out from the cards`. All three now
  say what the name shown was made from: the folder's name, or, where the cards sit in no
  season folder, the year on them, which multi-season mode names the season by. The page
  says to name the season under `[seasons]` or in the competition's section. A
  competition that shows no name at all is no longer counted among the values we worked
  out: `season.json` gives its season the provenance `empty`, and the page lists nothing
  for it.
- An absence is not a value we worked out. A division whose cards name nobody, such as one
  whose only card is a washout, had `players` and `captains` listed on the page as values
  worked out, with advice to correct names nobody had printed, and a division whose
  rosters mark no captain listed `captains` the same way. `season.json` now gives each
  such aspect the provenance `empty`, and the page lists neither.
- A captain the club declared reads as declared wherever the captain is printed. The
  captains' provenance was merged over the players record of each team's captain, so a
  declared captain on no counted card was left out of it: a division whose captains were
  all declared that way read captains `derived`, and the team page flagged the captain
  `The captain was worked out from a roster mark rather than declared`, telling the club
  to declare what it had. Each team's captain is now read as declared where the club named
  one and as the card's otherwise. The page's captains advice, which it shows only where
  some captains were declared and the rest read from the `(C)` their cards print, now
  says that, rather than calling the `(C)` a guess.
- The first run offers no number for the season's length. Over cards for rounds 1 to 7, it
  wrote `# rounds_total = 7` under `Remove the # to use it`, and the review list said
  `delete the # to adopt it`; a club that did as it was told declared the season over at
  round 7, the page read `Round 7 of 7` over `100% of the declared rounds`, and round 8's
  cards stopped the next run on F3. The line is now `# rounds_total =` with nothing after
  the `=`, under a comment saying to type there the number of rounds your association's
  draw gives the season, and the review list gives it a status of its own saying the same.
  Its offer to have the file written for you counts the line among those that land
  commented out, and says one with nothing after the `=` needs its value typed there too.
  Deleted alone, the `#` leaves a line the next run refuses as not valid TOML, naming the
  line, rather than a length nobody typed. The rounds played are still named, as a count.
- The date on a competition's card on the front page says what it is:
  **4 teams · 6 cards · generated 10 Sep**. A bare date beside the count of cards
  read as the night of the last card. It is the build that last changed what that
  competition's dashboard holds, the date the dashboard's own header and footer
  give, in the same word, and a rebuild with nothing new keeps it.
- A card with nothing on it, every set row 0-0, `VS` or no verb, no tick and a
  TOTAL row of noughts or none, is reported once, by its `no-result` warning.
  0.1.0 also told a night called off before anybody was entered
  `players-unrecorded`, and a night nobody entered a result for F6's warning
  that nothing on it confirms its set rows and `totals-missing`, each saying
  again in its own words that nothing was played. Those three stand down on such
  a card, each asking it the same one question, and the `no-result` of a night
  not called off now says its rows read 0-0 and, if the match was played, to
  enter its result in Match Centre and export the card again. The match's notes
  on the dashboard no longer say that a cross-check is missing either. A card
  that ticks Match Disputed over such rows is still told `disputed`, and what
  each empty rubber lacks, but no longer `verb-absent`. A card with games on it,
  a walkover, and a card whose verb, tick or TOTAL row names a result over blank
  rows keep every finding they had, and `tools/doctor.py` says which a card is.
- A walkover is scored as the forfeit it is. Match Centre exports a match one side
  gave up whole with `Match Cancelled` ticked, the side that came ticked `Match
  Winner`, and a TOTAL row of every rubber to that side as a love set: 4-0 and 24-0
  over four rubbers of `first-to-6`. 0.1.0 read every such card as a washout and
  failed the run on it, `A match called off has no winner, so the card contradicts
  itself`. A card printing exactly that, with no Match Forfeited box ticked and any
  verb it prints naming the side that came, is now a forfeit by the side that did
  not come, paid what a forfeit is worth (`forfeit_award`, below), its verb, tick
  and TOTAL row held to the walkover rather than to its rows, and its `Unspecified
  player` cells said to be a forfeit's. Rows of play its TOTAL row does not count, a
  night given up part way, are said once, as the warning `walkover-rows-played`;
  rows that, every set finished, give the absent side the match are
  `forfeit-contradicted`, as against the box. A TOTAL row that is not that award,
  or none, is a washout's, and so is `VS`, `DRAWS WITH` or a word this program does
  not know beside the tick.
- A card ticked `Match Cancelled` that also ticks a `Match Winner` box or prints a
  winning verb, over the rows of a night the rain stopped, no longer fails the run.
  Match Centre exports a washout that way, naming the side ahead when play stopped,
  and the cancelled tick takes precedence: the match is the washout it says, no
  result and no points, as before, and its one `no-result` finding is a warning,
  as it was before 0.1.0, saying what the card also claims and that the Match
  Cancelled tick decides. The README's advice to correct such a card in Match
  Centre, which a club could not act on, is gone.
- `players-unrecorded` on a match forfeited whole by its Match Forfeited box now
  ends `this match was forfeited whole, so nobody played those rubbers`, as a
  walkover's does. 0.1.0 said nothing more of a box forfeit's `Unspecified player`
  cells, and told one ticking `Match Cancelled` too that it was called off, though
  the match is scored as the forfeit.
- A side that every rubber's `Forfeit` box gives up, over rows left 0-0, is no
  longer asked for its roster, as a side the Match Forfeited box gives up is not.
- `set-player-count` no longer tells a match forfeited whole that a rubber nobody
  played is short of a name. The side that gave the match up, by the box or as a
  walkover, naming one player on a pair's 0-0 row was told that the rubber is a
  doubles set with 1 player(s) named; that row is now passed by, as
  `set-no-games` and `set-players-missing` already pass it, and a forfeit given
  up after a rubber is still held to the names on the rubbers it played. A
  played card's row left 0-0 with no box ticked is still held to its names: the
  card does not say nobody played it, and the players' figures count them.
- A pair naming one player above Match Centre's `Unspecified player` is read as a
  full side whose second place nobody filled. The reader's note `read 1 away
  player name(s) from a row its own marker '1&2' says is doubles`, word for word
  what a pair with its second cell left blank gets, is no longer among the
  match's Parse notes, nor printed twice by `tools/doctor.py`; the blank cell is
  still told. The match page follows the one name of a pair with `+ not
  recorded`, the placeholder's or the blank cell's alike, where it showed the
  name alone, as a full side reads, and so do the matches page's table of every
  rubber and its CSV.
- A played card's `Unspecified player` cells are reported once, by
  `players-unrecorded`, and no longer also by `set-player-count` and
  `set-players-missing` as names that failed to read; a pair printing it in one
  cell and leaving the other blank is still told `set-players-missing`, for the
  blank one. Its clause says, side by side, which rubbers have nobody to credit
  and which pairs name one player, half their games credited to nobody, where it
  said of every such rubber that nobody was credited. Where a rubber printing it
  was left 0-0, the clause begins `those rubbers with games have` and not `those
  rubbers have games`. Its detail names the side, `3 placeholder cell(s): away in
  rubber(s) 1, 3, 4`.
- A rubber ticked `Forfeit` against one side is scored as Match Centre's own TOTAL
  row counts it: a love set to the other side, 6-0 under `first-to-6` and 4-0
  under `fast4`, whatever games the row prints, and every rubber of a match
  forfeited whole likewise to the side that came, rows with play on them included.
  0.1.0 scored the games printed, so a card with three rubbers ticked against the
  away side, all three of which it won on court, 2-6 6-2 1-6 3-6, was 12-20 to the
  side that forfeited, failed the run on `totals-mismatch` and `verb-mismatch`
  against its own TOTAL row of 4-0 and 24-2, and paid 3.2 and 6.0 under
  `games-bonus`; it now builds clean, 24-2 and 6.4 and 2.2. Under
  `forfeit_award = "as-played"` the games printed stand and the tick still gives
  the set. A TOTAL row may total the rows as scored or as printed, and a verb or
  tick may go by either. The ladder's Games column, `games-bonus` and the players'
  games count the games scored; the match page says the games played beside them,
  as `Forfeit, 2–6 on court`, and so does a player's list of sets; a rubber left
  0-0 under a forfeit, which nobody played, is in no player's figures and is
  counted in the team page's caption; and the overview's Games played counts the
  games printed, its tiles leaving out a night not one rubber of which was played,
  and its Sets completed a rubber nobody played. A washout's rows are scored so
  too, as its TOTAL row counts them, though the night still counts for nothing.
- A set one side's `Retired` box is ticked on is the other side's, its games
  raised to win it where they were fewer: 5-1 with the away box ticked is 6-1 to
  home, 4-2 with the home box 4-6 to away, and 0-0 with the away box 6-0. 0.1.0
  gave every retired set to nobody, so a card printing Match Centre's 4-0 and 24-8
  over a retirement at 5-1 failed the run on `totals-mismatch`, and a night whose
  result needed every rubber had none. Both boxes ticked is nobody's set, at the
  games printed, and the new warning `set-retired-both`; `Incomplete` is nobody's,
  as before; under `as-played` the set is still the other side's, at the games
  printed. The demo's retired-set card prints a verb and ticks a winner where the
  retirement decides the night.
- `set-forfeit-contradicted` is no longer said of a row ticking `Forfeit` against
  the side it shows winning when the card's TOTAL row counts the row as Match
  Centre does, a love set to the other side, since the card then agrees with
  itself. Said, it now says what the row is scored: `scored 6-0 to` the side that
  stayed `as forfeited`, or `credited to` it under `as-played`.
- The first run offers no home court that no card gave. Its `courts` table wrote
  `# "Bramble Finches" = 1` for the first team by name in every competition, from a rule
  in the writer that no card prints a court, though every card prints the court it was
  played on: deleting the `#` declared court 1 for a team whose home cards print court 5.
  The table now offers the court the cards print (see Added), and keeps that sample line
  only for a competition none of whose cards prints a court.
- `--discover --force` over a config whose declared draw closes its team list wrote the
  list it offered under `# The full list of teams in this competition, as read from the
  cards.`, though the list left out a team the cards name, the one the run's F8 reports.
  The comment now says the list is as the declared draw names it, and the list with that
  team follows it behind a `#`.
- The test that nothing published names a particular club or player reads each line as
  a slug folds it, its case and accents set aside, and asks each term it is given as
  written and as its slug, so a name written with an accent its listed spelling has not,
  or written as a slug, is found; the terms it is given may be match ids. The examples of
  the reader's comment on the identity line, of the parser and doctor tests that read
  it, of the slug test and of this file's 0.1.0 entry on the identity line are a made-up
  match id, `1000100`, and a made-up name, where 0.1.0 used ones from a real club's
  cards.
- The test suite passes in the copy you build your dashboard in, with your cards in
  `data/`, your `config.toml` and the `site/` a build leaves there. Until now, in a git
  clone where `tools/demo.py` and `build.py` had run, it failed three tests: the
  README's block for a folder with no cards, because `build.py` rightly pointed at the
  cards beside it; the test that no club's config ships, which found the `config.toml`
  git ignores; and the test that every command can say its version, which found the
  demo's cards already in `data/`. A virtual environment in `.venv/` failed one more,
  and the lock file Emacs leaves beside a file it has open, a link to nothing, gave
  eighteen errors. The tests now ask git what ships, or run the program in a fresh copy
  of itself, and read a link as a link. `.venv/` is gitignored, since before Python
  3.13 a virtual environment brings no ignore file of its own and `git add .` would
  commit pip, and the test that nothing published names a particular club or player no
  longer reads a `config.toml` git ignores. A test makes a club's copy, a clone with
  the demo's cards, a build, a `.venv/` and a lock file, and runs there the tests that
  read the folder, each of which must pass without skipping. The suite's own git no
  longer takes a repository from the environment, so run from `git rebase -x` or a
  commit hook it makes no commit or tag in the repository running it and leaves its
  index and settings alone. A folder unzipped from a release, or a computer without
  git, has no git to ask, and there one test still fails once a build has written
  `config.toml`: run the suite before the first build. README section: *Checking the
  tool itself*.

### Added

- The data health detail on a dashboard can show its errors alone, its warnings
  alone or both. Above the list, where it holds both kinds, three radio buttons
  under **Show** read **Both**, the errors and the warnings, each counted in the
  summary's words, so a card the build refused is counted apart there as the
  summary counts it. A choice hides the other kind and changes no count. The page
  opens on **Both** every time and keeps no choice from one visit to the next, and
  an open list prints whole whatever was chosen.
- `[rules] forfeit_award` (`full-games`, `fixed` or `as-played`) and
  `forfeit_points`: what a match forfeited whole is worth, overridable under
  `[competitions.<id>]` and written behind a `#` by the first run. The default is
  `full-games` under every preset: a win with every rubber a love set to the side
  that turned up, and 0 to the side that gave the match up, which is 6.4 and 0
  under `games-bonus` over four rubbers of `first-to-6`, where 0.1.0 paid 4.0 and
  2.0, as played. `fixed` pays `forfeit_points` and 0, and stops the run without
  it; `forfeit_points` under another award is reported as not read; `as-played` is
  0.1.0's pricing. The Match Forfeited box, every rubber 0-0 and ticked `Forfeit`
  against one side, and a walkover are paid alike. Whenever a forfeit is not paid
  as played, the run report's scoring line and the dashboard's rules banner say
  what it is paid.
- **Every player this season, on one page.** A single-season build that prints
  players' names writes `careers.html` too, as *Players this season*, linked from
  the front page after the last competition and from every player's page (*Season
  across competitions →*). It lists everyone the cards name, by name and not as a
  ranking, with a row for each competition they appeared in, the team whose
  roster they are on there, and a total; every row opens that competition's page
  for them. So a player entered in a Monday and a Tuesday division, or a fill-in,
  has their season added up in one place. The report's `cards` section says
  `careers      2 of 11 player(s) played in more than one competition`. The page
  keeps its file name, so switching to multi-season mode keeps every link to it,
  and switching back rewrites it rather than listing it under *left behind*. A
  build that withholds names writes no page and no link, and its `careers` line
  says why in single-season words: a withheld label belongs to one competition,
  not to a person. Such a run over a site that printed names lists the page under
  *left behind*, since it still prints every name, and exits 1 until `--prune`
  removes it, as it does for a dashboard left with names. A one-file dashboard
  has no link to it. A `careers.html` of your own in `site/` now stops a
  single-season build that prints names, as it stops a multi-season one, and is
  left as it is. A `[namesakes]` line naming teams of two competitions is no
  longer called useless in single-season mode, because on this page it tells two
  people apart.
- `python3 build.py --discover` over an existing `config.toml` writes a proposal beside it,
  `config.proposed.toml`, and leaves `config.toml` as it is. It had refused, exit 2, before
  reading a card, and the one way to ask the cards what they would write was `--force`,
  which writes the file fresh and drops a declared draw and every comment. The run now
  builds the dashboard as a plain run does, with the same exit status, and the proposal is
  the file `--discover --force` would write, under a header that says which run wrote it,
  that nothing reads it, how to adopt it, and what adopting it whole loses. The review
  section opens with what it offers that `config.toml` lacks: competitions it does not
  declare, teams a declared list or draw lacks, captains (a blank one among them) and
  home courts where it names none or another, and any other value it does not hold, each
  with its lines; or it says the proposal offers nothing `config.toml` lacks. It writes
  over a proposal of its own, and leaves one that holds what it would write already as it
  is, date and all, as the site's pages are. It refuses, exit 2, a file of that name it did
  not write, or cannot read, and says which. `.gitignore` ignores `*.proposed.toml`, since
  a proposal holds every name `config.toml` does. `--config club.toml` gets
  `club.proposed.toml`. `--check --discover` writes nothing, and `--discover --force`
  writes over `config.toml` as before, with the proposal's body and so its offers.
- The first run offers each team's home court from its cards. The `courts` table writes,
  behind a `#`, the court each team's home cards print most, in the words the captains
  are offered in, with a comment naming each court and on how many cards where they print
  more than one. Two courts printed equally often are named and neither is offered, and a
  team with no home card gets no line. A court the club declared is written back live,
  with the court its home cards print below it behind a `#` where the two differ. The
  dashboard shows a home court only once it is declared.
- A pushed version tag makes the GitHub Release. Pushing a tag had run no workflow,
  and the notes were pasted by hand. `.github/workflows/release.yml` runs on a tag
  starting with `v`: `tools/release.py check` refuses in seconds, with every reason at
  once, a tag the code and the changelog do not both describe as released; the suite
  runs as `tests.yml` itself, which a workflow may now call; and `tools/release.py
  publish` zips the tagged tree as `matchcentre-dashboard-X.Y.Z.zip` and makes the
  release with `gh`. Its notes are the changelog's section, each wrapped paragraph and
  item on one line, then the zip's SHA-256 and the run that tested it; `check` refuses
  notes longer than the 125,000 characters GitHub takes. A re-run, of the same tagged
  commit, finishes a release that stopped half way and publishes one left a draft; a
  run whose tag has since moved on GitHub publishes nothing; and a version with a
  pre-release part is published as one. `tools/release.py prepare X.Y.Z` writes the
  version, moves `[Unreleased]` under a dated heading and rewrites every example of
  the report's first line in the files git keeps, which the suite had found only after
  the bump; a final after its own release candidates gathers their changes under it
  too, leaving their sections where they are, and a write that fails part way leaves
  each file whole and puts back the ones already written. `publish --dry-run` writes
  the zip and the notes into `dist/`, which git ignores, without making a release.

### Changed

- A ladder ordered on the points average, as `games-bonus`'s is, names its leader
  while teams have different numbers of matches counted. The average divides each
  team's points by its own matches counted, and under the default
  `unplayed = "zero"` a bye, a cancelled match or a washout is in neither, so the
  order already stood. The front page now says `<team> lead on <average>`, and the
  top of the overview names the same team, where the front page said the ladder was
  in progress. Such a ladder in a division of an odd number of teams, a bye every
  round, is led all season but for a tie at the top (below), where it was led only
  after the rounds the byes came out even. The note under the ladder says the order
  stands rather than calling it a snapshot, and the F10 warning still names the
  teams with fewer matches counted, in case one is a card not yet copied in, and
  says it costs them no place; a team with no match counted yet is still placed
  last, and the warning and the note say that of it. Two teams level on the average
  with different numbers counted are put in order by the next thing the ladder is
  ordered by, under `games-bonus` points, a total. There the warning says the tie
  goes to points and that having counted fewer, by one match or more, places the
  team below, and the note says that part of the order is a snapshot and names what
  the tie goes to. If the team on top is level so, nobody is named the leader, and
  the front page and the overview say it is for that tie and not for the counts.
  `season.json` says which kind of order it is as `meta.ordered_on_average`, whether
  it stands on the counts it has as `meta.order_stands`, and why a leader is
  withheld for a tie at the top as `meta.leader_withheld`; the document format is
  still `4`. A ladder ordered on points (`sets-first`, `rubbers` and `simple`) names
  nobody until the counts are level, as before, since the team on top there may be
  on top for having played more.

## [0.1.0] — 2026-10-01

Initial release.

### Added

- `build.py`: the single command. Reads every PDF under `data/`, groups the cards
  into competitions, scores them, and writes one self-contained dashboard per
  competition into `site/` plus a front page listing them all. Options:
  `--data`, `--out`, `--config`, `--check`, `--discover`, `--force`, `--quiet`,
  `--generated`, `--single-file`.
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
- A card whose letterhead is not the one the rest of the competition's cards print
  reported by path, with both spellings, as `foreign-card`. That is what routing on
  card contents costs: a PDF exported from another club's Tuesday B division lands
  in yours, brings its teams and players, and takes a ladder position, and the
  letterhead is the only thing on it that says otherwise. It is a warning and the
  card is still scored, because the same club prints its own name two ways often
  enough that refusing a night's tennis over one of those would be worse than
  saying so; and a letterhead that did not read at all is not counted as a second
  club.
- One night, one result. A fixture is counted once however many cards arrive for it,
  which is what the ordinary accident needs: a card is exported, a score is fixed in
  Match Centre, the card is exported again, and both files sit in `data/`. Scoring
  both hands two teams a second win and twenty more games, in the ladder, in both
  team rows and in the playing record of everybody named. Two exports that say the
  same thing are one night's tennis and are counted once, reported as a warning that
  says the figures are right and a spare file wants deleting. Two cards that
  disagree are counted nowhere — not in the ladder, not in either team's row, not in
  anybody's record — and reported as an error naming what they disagree about, since
  choosing between two results is a guess this program does not make. Every match on
  the page carries the reason it was left out of the figures above it, and the team
  page explains a re-export as a re-export rather than as a final.
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
- Whether a night can end level at all as a setting of its own — `draws` under
  `[rules]`, overridable per competition, TOML's own booleans and nothing looser.
  Every preset allows a draw, and plenty of associations do not: they decide every
  match on a tiebreak, a countback or a by-law, so a level card there is a result
  nobody recorded rather than a draw. Under `draws = false` such a night is reported
  as an error naming the card, marked `Level` on both teams' pages with a sentence
  saying why, and left out of every ladder figure — played, points, percentage, and
  the head-to-head that breaks a tie — while the rubbers on it still count in every
  player's record, because they were played. The dashboard is still written and the
  rest of the season is unaffected: one card a grade's rules cannot price is a card
  to report, not a reason for a build to fail. `Ruleset` could express this from the
  first commit and nothing could reach it, so a scheme with no value for a draw also
  described itself as though its draw were worth nothing; the run report and the
  page banner now say `no draw`, and say what becomes of the card, in one sentence
  from one function.
- A match that was not played contributes nothing anywhere, including when part of
  its card was filled in first. A washed-out night regularly stops partway: two
  rubbers finish, the rain arrives, and Match Centre keeps what was entered, so the
  card arrives with real games on it and a ticked `Match Cancelled` box. Those games
  are not a partial result to be counted at reduced strength — they are counted
  nowhere. Not in a team's games or sets, not in its home-and-away split, not in the
  per-slot table, not as a match played or a match available, and not as an
  appearance on any player's record. One predicate decides it, `Match.counts_as_played`,
  consulted at every aggregation site rather than restated at each; the browser
  reads its answer off each match, in `countsAsPlayed`, so the tiles a page computes
  for itself cannot disagree with the ladder printed beside them. A match played to
  the end with one rubber left unfinished is the opposite case and is unaffected: it
  happened, so its games count and it takes a ladder result if enough rubbers finished.
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
- A rubber with no games on it reported as a rubber nobody played rather than as a
  set that finished level. A night rained off after two rubbers arrives as an
  ordinary card with empty rows whenever nobody ticked `Match Cancelled`, and
  describing those rows as level at 0-0 describes a contest that did not take
  place. They are reported as `set-no-games`, separately from a rubber that
  genuinely stopped level, which keeps its score in the message; a card whose every
  row is empty is still reported once as a fixture that has not happened, rather
  than row by row.
- Configuration in `config.toml`, read with `tomllib`. A run that finds no config
  writes one, filled in from what the cards revealed, with every worked-out value
  written commented and annotated with what confirming it would change — and
  builds the dashboard anyway, so nothing has to be configured before the tool
  can be seen working. `config.example.toml` documents every setting.
- Rules against recording absence of evidence as evidence: no bye inferred
  without a confirmed team list; no unplayed fixture invented from a draw read off
  cards; no season length assumed from rounds played; every PDF found either
  scored or reported by path; a fixture counted once however many cards arrive for
  it, and counted nowhere when two of them disagree; a card naming a team outside a
  confirmed team list
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
- Finals kept out of the ladder in one place and by one rule: the figures the ladder
  rests on are the regular season, a player's own record is every match they played,
  and the Share column follows the team row because it is a partition of it. So a
  semi-final is on the Matches page and in its players' records, and in none of the
  ladder points, team tiles, home-and-away split, form or points chart — the page
  reads that from the document rather than re-reading round labels, and says on the
  team page which listed match is in no figure above it. A numeric round label is
  never treated as a final, since promoting round 10 would drop a real round out of
  the ladder. A round correctly excluded is reported as a warning naming the round,
  not as an error, and the report says what to change if the round is a regular one.
- "Share of the team's games won" as a true partition of that team's games: a
  doubles rubber was won by two players together, so it is split by the slot, half
  each, and held to a half rather than rounded to a whole. Where a card leaves a name
  off a doubles rubber, or names somebody no roster block registers, the column is
  left short and the games are credited to nobody — crediting them to whoever was
  printed would put a person in a rubber their card does not name, or a stranger in a
  squad they may not be in.
- A level match says what decided it, and only where something did: the sentence is
  printed from the document's own two statements — that the match was level on games
  and that sets separated the sides — so a night nobody played no longer reads "tied
  on games, decided on sets" over a 0-0, and a forfeit names no basis at all, its
  scoreline having been awarded rather than played. Which basis is named follows the
  scoring scheme's own order of tiebreaks.
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
- A folder of cards that would not read is answered as its own problem, not as an
  empty folder. "Export a match and put it in `data/`" is the right thing to tell
  somebody with nothing there and the wrong thing to tell somebody whose cards are
  already there and were each named and refused higher up the report, so the second
  case now says how many were found, that none of them read, and where to look — and
  puts the count in the `exit 1:` line too, since that is the line a club reads first
  and often the only one a log keeps. Both reports are quoted in the README and both
  are checked against a real run.
- The terminal and the dashboard count the same cards. A card the reader understands
  and the filing cannot place — both banners naming the same team, no set rows to
  score — was named in the written page and nowhere in the report, which counted it as
  scored; the report and the page now work from one list, so `scored` is the cards the
  dashboard actually holds and every card found and not scored is named with a reason
  and counted in the last word. Since `pages seen` is counted by the reader before any
  of that, the two lines under it can be checked against it: a page in neither is
  reported as a fault in this program, not in the club's cards.
- Two `[competitions.*]` sections claiming the same day and division are refused,
  naming both. Cards are matched on those fields rather than on the section name, so
  only one of the two could ever have them, and the other's team list, captains, byes
  and round dates would stop taking effect with nothing on the page to say so — a
  closed roster quietly reopening and its bye credit going with it. The refusal offers
  both fixes: delete or merge the leftover section, or, for two seasons of one
  division, tell them apart with `season_key`.
- A download that never finished is reported as a missing card rather than skipped.
  `card.pdf.crdownload` and its equivalents in every browser are not PDFs, so the
  reader never saw them and a club that exported seven cards got six and an exit 0.
  They are now named, explained and counted like any other card found and not scored.
  Chrome's `Unconfirmed 402631.crdownload` counts too; `.part` and `.tmp` need the rest
  of the name as evidence, so nobody's scratch file is called a missing scorecard.
- Everything else under `data/` that is not a PDF is listed once, as files that were
  left alone. Keeping your own draw spreadsheet there stays fine — this is a note, not
  a refusal, and it is absent when there is nothing to say — but a club that saved the
  Match Centre page instead of exporting it now gets a line naming the file rather than
  a run that read nothing and never mentioned it.
- A dashboard that could not be written no longer takes the report with it. The pages
  are the last thing a run writes, and a folder owned by another user, a full disk or a
  read-only drive stopped the run with one line of errno — discarding every card read,
  every refusal, the ladder and the data health, all of which had already been worked
  out. The whole report is printed now, and the ending names the file, gives the reason
  in words as well as by number, and says what to do about that particular reason. It
  does not claim the folder was left untouched, because a failure part way through the
  competitions is not, and it exits 2 — a refusal — rather than the 1 that means the
  dashboard is there with something to fix.
- Under `unplayed = "average"`, the ladder now shows the figure its `Avg` column
  divides by. `P` counts the matches a team's result was decided in — `W`, `L` and `D`
  add up to it — and the average divides by those plus every round credited the team's
  own average, so points ÷ `P` was not the number printed and nothing on the page said
  why. A `Counted` column appears between `Points` and `Avg` wherever the two figures
  differ, with a caption saying what it is; the points-average chart prints it in its
  data table and its tooltip; and a team's own page says it under its ladder position.
  Under the default `zero` the two are the same number and the column stays away. The
  line the report and the dashboard both print against `unplayed` no longer says a
  credited round "counts as a match played" — it counts in that average, and calling it
  a match played contradicted the `P` column beside it.
- A match that was played and reached no result — rain with the `Match Cancelled` box
  never ticked, or a forfeit with neither side's box ticked — now counts as a meeting in
  the head-to-head figures. It was skipped, so the grid printed a blank cell whose hover
  read "not played yet" about a card listed on the matches page, and that night's games
  were in both teams' `GF`/`GA` and in no pairing. The wins, losses and draws still count
  the decided meetings only: the difference is a new `undecided` figure, said in the grid's
  caption and in a `No result` column on the team page wherever there is one. Cancelled and
  unplayed cards still count nowhere. The ladder's head-to-head tiebreak is unchanged and
  deliberately still asks only who beat whom.
- The head-to-head record now says how many of two teams' meetings were finals, and the
  page says what that means. Finals are out of the ladder (F11) and in the pairwise
  record, which is the right answer to each question — a semi-final is a night two teams
  met, and is not a round of the season being ranked — and two figures on one page worked
  out over different matches that nothing accounts for is the same defect as a figure
  that is simply wrong. Sharper than it sounds: two teams can be level on the grid, one
  of them ahead on games, and first and last on the table above it. The grid's caption
  and the opponent table on a team's page now name the count, and where the ruleset
  breaks ties on `head_to_head` — every preset does — the ladder's own basis line adds
  that head-to-head there means the regular-season meetings only. All of it appears only
  once two teams have actually played a final, so a grade without finals pays no line
  for it.
- The warning about a doubles rubber with one name printed now names the card. It carried
  the name that *did* read and no file anywhere, which is the wrong way round for the one
  player finding a club fixes upstream in Match Centre rather than in the dashboard: the
  half they already have was the half they were given. The name is now in the sentence,
  next to the count, and the PDF's path is the supporting detail, as it is for every other
  finding a club acts on by opening a card.
- Two cards for one fixture that agree about the result and disagree about one name no
  longer cost the two teams the night. That is what a club does when it fixes a card in
  Match Centre — an `Unspecified Player` filled in, or the wrong Modric — and exports it
  again beside the original: every total agrees and one cell does not. Treated as a
  contradiction, the ladder lost a result neither card disputes, so a club was charged a
  round of tennis for correcting a spelling. The result is now counted once, because both
  cards say the same about it, and no appearance is credited for the night, because
  crediting the card read first would put a real person in a rubber the other card gives
  to somebody else. The two questions a card answers now have their own fields — the
  ladder's `not_counted` and the players' `not_credited` — and the page says the second
  one out loud in three places: a sentence on both teams' pages, a clause in the squad
  table's caption where the missing match shows, and a note on the card itself under the
  names it read. It is still an error with a non-zero exit, because everybody's record for
  that night is short until the wrong card leaves `data/`. Agreeing about the result means
  agreeing rubber by rubber, not agreeing on the two totals: two cards that swap the
  winners of two rubbers still disagree, and are still counted nowhere. The set-by-set
  trail on a player's page reads the new field too, which also fixes a night listed twice
  there for any fixture whose card was simply exported twice.
- A run that reads no cards no longer says it worked the names out from them. The note
  explaining that every name, captain and season label came off the scorecards themselves
  was printed on runs with an empty `data/` folder too, where it read "worked out from the
  0 scorecards themselves" with no name, captain or label anywhere under it. It now says
  what is true of that run and still worth knowing on it: nothing was read, so nothing was
  worked out; `config.toml` is where the club name, the team list, the draw and the scoring
  scheme go; and none of it is needed to start, because nothing there can change a score
  and the first card brings its own names with it.
- A `config.toml` the program cannot open now says what to do about it. Both directions
  came out as `error: [Errno 13] Permission denied: 'config.toml'` and nothing else, and
  the writing side did not even say a config file was being written — just a number and
  a path, on a run given `--config`. Each reason now gets the sentence for it, and the
  two directions get opposite advice, because they are opposite problems. A config that
  cannot be **written** is the program's own suggestion, filled in from the cards: write
  it somewhere you can with `--config` and copy it back, and nothing you entered was at
  risk. A config that cannot be **read** is your settings, and pointing `--config` at
  another file is the one thing not to do — the scoring preset lives in there, and a
  build without it is a ladder computed under rules nobody chose. The commonest cause is
  named, because this program creates the file: one run under `sudo` leaves a config your
  own account cannot read. A config that cannot be **read** is a refusal like every other
  refusal in this program — exit 2, nothing built. One that cannot be **written** is not,
  which is the bullet below.
- A `config.toml` that cannot be written no longer takes the evening's tennis with it.
  That file is written two statements after every card has been read, filed and scored,
  and one statement before any of it reaches the report, so a folder the account cannot
  write — or a file standing where the folder for it goes — ended the run at exit 2 with
  nothing printed and no dashboard, over a file this program had offered to write from
  cards the club already had. Nothing the build does depends on it: the dashboard is built
  from the settings as they were *read*, and on a first run those are the defaults. The
  refusal is now carried into the report, which names the file, the reason and the fix for
  that reason, says in words that the dashboard is unaffected, and offers it to open. What
  the refusal still changes is the exit status, because a nightly build that quietly
  stopped writing its config is a run whose exit status has stopped meaning anything:
  exit 1 on a plain run, and exit 2 under `--discover`, where writing that file is the only
  thing the run was asked to do. Every way a run can end is now written in one place, so
  the `exit N:` line and the status returned cannot drift apart, and that line says both
  what the cards came to and what was lost beside them.
- A `config.toml` key this program does not recognise is now told which one it nearly
  is: `[clubb] is not a section this program understands and was ignored. Did you mean
  club?`, and the same for a setting inside any section, drawn from the names that are
  legal where the key was written. Within two edits only, and where two names are equally
  close both are offered; nothing is ever corrected for the club, because a setting that
  took effect under a spelling nobody wrote is a setting nobody can find again.
- The `== rules in force` banner and the config warnings under it are wrapped to the
  width of the report. The banner's descriptions reached 110 columns and the warnings
  more, so on an eighty-column terminal the second half of the one section a club is
  asked to read and act on was folded hard against the left margin, where it read as
  another field. They now wrap under their own column, and the warnings are printed
  under a `config` label like every other field of that section.
- And now the rest of the report with it. Twenty-eight lines of a demo run ran past the
  width the report draws its rules to, the widest of them 437 columns: every note about
  something the run worked out, the `why:` under each config value it offers, the
  generated `teams` and `fixtures` lines, each refused card's reason, and the body of
  every warning. A terminal folds those rather than dropping them, and a folded line
  comes back against the left margin, where it reads as another finding — one note came
  back as six. Continuations now sit under the text they belong to, or hang two where the
  label is a division's own name and aligning under it would either squeeze the words
  into a column or indent them past the lines underneath that belong to them. A path or a
  config key too long for the line is left whole and allowed to run over, because it is
  the one kind of word that has to be exact.
- The `teams` and `fixtures` lines the report offers are broken between items, never
  inside one. They are TOML a club pastes into `config.toml`, and wrapped like prose they
  would arrive as a file that will not parse — or, split down the middle of a fixture, as
  one that parses with two teams playing nobody. A comma inside a team's own name is not
  a place to break, every item is left with a trailing comma so that adding a team is
  appending a line, and a name too long for the line is left long rather than cut in two.
- And now the `config.toml` this program writes, which was never measured against the
  width it prints everything else to. Six lines of a demo season's file ran over, the
  widest 165 columns: the commented `format` line, which carried all ten match-shape names
  in a trailing comment after the value — names a club has to spell exactly, in the one
  place a terminal folds first. They now sit in a wrapped list above the line. Three more
  were rows of the option tables, `games-bonus` among them, which describes the setting
  the report reprints on every run because getting it wrong reorders a ladder; those fold
  under their own description column, so the column of names stays a column and a wrapped
  sentence cannot be read as another preset. The `teams` array, the draw example and the
  worked-out byes go through the same writer the report's TOML goes through, so they break
  between items and never inside a name, and a commented array comes back commented on
  every line of it. Which changes what a club is told to do with it: a team list that no
  longer fits on one line needs a `#` deleted from each of them, and deleting one leaves a
  file that will not load, so both the file and the run's own note now say *remove the #
  from every line of* when that is the case and *remove the #* when it is not — computed
  from the array in one place, rather than worded twice. A worked-out bye's `# already
  worked out from the cards` is now said once above the group instead of on each line,
  where long team names pushed it off the edge.
- The README was the third place that said it, and the one that was already wrong. Its
  "Reviewing `config.toml`" section showed a team list this program does not write — the
  one-line form folded by hand, with `...` in it — over a sentence it had stopped writing,
  above the instruction to delete the `#` at the start of the `teams = ` line. Followed
  exactly, that is one deletion of four and a file that will not load. It now shows the
  block a first run really writes, and two tests hold it to that: the quoted block has to
  equal the commented run the documented first run produces, closing bracket included, and
  the number the prose asks a club to count ("four lines above") has to be the number of
  lines that run actually wrote.
- The eight paragraphs built around a folder name now wrap around it. The report was
  measured at eighty columns using this project's own short paths; a club that keeps each
  exported season in its own named folder prints one of a hundred, and what went over the
  edge with it was everything said after it — `and run this again`, `Any folder layout
  underneath works`, `(double-click works; no web server needed)`, `or the part of this one
  that got as far as writing did`. The path itself may still run over, because a club can
  scroll a line it cannot retype, but nothing is printed behind it now, and a line over the
  width has a word in it no wrap could have helped. `open` became a wrapping line for that
  reason, while `wrote` above it stayed a plain one: its path is the last thing on it. Two
  more turned up in the measuring — the `--discover` refusal's advice ran to 118 columns on
  its own words, with no path in it at all, and the one-line `error:` on stderr is the
  deliberate exception, kept on one line for a cron job that greps it, and now held to
  carrying both the file and the reason.
- A fixture is in the draw once. The three places that reconstruct a round's fixtures took
  them a card at a time, which is a fixture at a time only until a club exports a night
  twice — and then the draw grew a fixture nobody could ever play: the Draw page listed the
  night twice under its round, and "of N in the draw", the figure a club reads its season's
  progress against, counted a night that was never going to be played, so a finished season
  could never read finished. A night two cards *disagree* about is still a fixture, and
  deliberately: they disagree about the result, not about who played whom, and dropping it
  would take the night off the one page a club opens when a night is missing. The two sides
  the other way round are still two fixtures, because merging them would mean this program
  picking which team was really at home — a team down to play twice in a night is an error
  it reports by name instead. And a club's own declared draw with a fixture written down
  twice, which nothing used to mention at all, now gets a note naming the round, the fixture
  and the table to edit — along with the quieter cause, two spellings of one team that come
  out as the same team.
- The five figures the front page opens with now count each night once. Matches played,
  sets completed, games played, the tightest night and the biggest margin were counted off
  every card, so a night exported twice was two nights in all five — one more match played
  than there are fixtures in the draw, and that night's sets and games counted twice
  directly above a ladder that counted them once. Two cards that disagree about a fixture
  were in these figures with both readings, and in no other figure at all. Each match row
  now says whether the document's own totals were built from it, which is a third question
  and not either of the two a row already answered: a final and a level night are in no
  ladder figure and were played, and the kept card of a night two cards name differently
  credits nobody and is still the night both teams played. Where a night is set aside the
  word is the ladder's own word for it, so the page has one vocabulary and not two.
- A finding says which competition it belongs to once, not three times. The report prints
  every finding under that competition's own heading and the dashboard's data-health list
  sits inside that competition's page, so ten findings that named it themselves — an empty
  team list, a season length nobody declared, a ladder still in progress and seven others —
  read `tuesday-division-b: tuesday-division-b: …` with `subject: tuesday-division-b` on the
  line below. It stayed hidden while those lines ran two hundred columns wide and the second
  naming was off the side of the terminal. Each now opens with a subject of its own rather
  than with the label above it, and the two places the competition's name is genuinely part
  of what is being said — the TOML path a club has to open to declare a finals round, and the
  team-list offers, which are one list across every competition in the run — still say it.
- A division named twice over on the card — `Division 3 Grade B`, which is how a good
  many associations write it — is read whole. Only the first word after the keyword used
  to be read, so `Grade A` and `Grade B` of one section both read as division `3`, shared
  a competition id and shared a ladder: two grades of teams ranked against each other,
  every figure right and the standing meaningless, with nothing in the report to say so.
  A division still ends where its own words end, so a ball colour, a round and a match id
  on the same line are not part of it.
- A division that leads with a number and names its grade after it is labelled
  `Tuesday Division 3 Grade B` rather than `Tuesday 3 Grade B`. One that already leads
  with a word of its own — `Section 3 Grade B`, `Div. 2` — is still printed as the club
  wrote it.
- A pairing marker printed with spaces around its separator — `1 & 2`, and the same for
  `+`, `/` and `,` — is read as the pairing it is. The marker column was read one printed
  word at a time, so a spaced pairing offered a `1`, a nothing and a `2`, and a doubles
  rubber with two names a side was filed as singles. A whole card of them read as
  `singles-4` at exit 0, and the run then offered that as the format to pin, with its only
  complaint being that the rows named too many players. A cell that holds more than the
  marker — `Doubles 1&2` — still reads, a cell holding two readings still says so instead
  of picking one, and `1 2` with no separator at all is still not read as a pairing.
- In multi-season mode the front page lists the newest season first in two cases it used to
  get backwards: a season key another key starts with (`2026` above `2026-finals`), and a
  numbered season (`2025-season-9` above `2025-season-10`). Season keys now compare digit
  runs as numbers and ignore capitalisation, so `data/2026-Winter/` sits where
  `data/2026-winter/` would. The order is taken from the folder name under `data/`, never
  from the season's label, which is often the same words for two different seasons. Two
  keys that compare alike, such as `season-1` and `season-01`, are two seasons and run
  in text order. They had tied, so the front page put one's sections between the
  other's, and the `--only` report's *not listed* line and its list of seasons changed
  order with the hash seed. A site with two such seasons, rebuilt, rewrites its
  `index.html` and `picker-data.js`.
- The dashboard says so when the season document it opened was built to a different
  shape from the one the page reads — including a document that names no shape at all,
  which used to pass in silence even though it was necessarily older. The exporter and
  the page hold that version number in separate files, so a test now checks the two
  against each other rather than trusting them to be edited together.
- Every word on the dashboard is legible, and a test computes it rather than trusting
  it. 188 colour pairings were under WCAG AA's 4.5:1, across 29 rules and every palette
  the stylesheet declares, and they all came the same way: a colour chosen to be a *mark*
  — the amber dot beside a warning, the red rule under a rejected card — reused for the
  *words* beside it. A mark has to be found, which is settled at 3:1; a word has to be
  read. Amber at 1.64:1 against a table header is a perfectly good dot and an unreadable
  sentence, and the sentences it carried were the caveats: a ladder in progress, a name
  the tool had to work out, a card that produced no result. Every status colour now has a
  text twin (`--warning` beside `--warning-text`, as `--good` and `--success-text` always
  were), the muted grey the ladder position and every footnote is printed in clears 5:1 on
  the darkest surface it can sit on, and the number inside a filled bar picks its ink
  against the fill's own luminance rather than against a threshold that put a 600-weight
  figure on the blue ramp at 2.50:1. With no browser on the machine that builds this, the
  check is arithmetic: a new structural reader parses the stylesheet's rules and palettes,
  and its contrast formula is held to eight published pairs so it cannot agree with
  itself. Print is checked as an overlay on each theme rather than as a palette of its
  own, because a page is printed in whichever theme it was being read in.
- The dashboard's *Skip to content* link no longer throws away the page it was pressed
  on. A skip link has to name the id of the region it skips to, and this dashboard keeps
  its route in the fragment too, so the browser's own jump set the address to something
  the router read as a route and could not parse — and answered by rendering the Overview.
  Tab then Enter, the first two keystrokes available to somebody who cannot use a mouse,
  discarded the view they were reading, scrolled them to the top, marked a tab they had
  not chosen, and left the URL with no route, so a refresh or a shared link landed on the
  Overview as well. The link now moves focus without touching the address, wired before
  the season loads so that it still works on a dashboard that could not find its data —
  the page a reader most needs to reach on a keyboard. No other link on the page may be an
  in-page anchor, which is checked, because a second one would be the same bug again.
- A dashboard served over http no longer downloads its season twice. Every competition
  directory holds the same document in two files: `season-data.js`, which the page loads as
  a script because a browser refuses to read a sibling file when the page is opened by
  double-click, and `season.json`, the same numbers as plain data for anything else that
  wants them. The page asked the network for the JSON even though the script had already put
  the whole season in memory: on an eight-team home-and-away season that was 829KB of a
  1,033KB page spent on a 415KB season. It now reads what it already has and fetches only
  when no script copy arrived, and the footer names whichever file it read. The script copy
  is also written on one line now — nothing reads it by eye, and the indentation was 44% of
  it. `season.json` keeps its indentation, in the same key order, so it is still the copy to
  open, diff and hand to something else. The two together: 233KB of season where there was
  829KB, and 437KB of page where there was 1,033KB.
- The topbar no longer covers the thing the page has just scrolled to, and no longer takes
  a third of a phone screen for itself. It sticks to the top of the window, so every scroll
  the browser makes on the reader's behalf — a skip link, Shift+Tab to a control above the
  screen, an in-page link, Find in page — landed its target at the top of the page, which
  is exactly where the bar is. Measured at 360×640: 186 pixels of the target behind the
  bar, which on that screen means all of it. A mouse nudges the wheel and finds it; a
  keyboard reader is told they have arrived somewhere they cannot see. The page now holds
  the bar's height clear at the top of every such scroll, measured from the bar itself
  because it is 82 pixels tall on a desktop, 128 where it wraps to two rows and 187 on a
  phone. *Skip to content* asks for that scroll explicitly, rather than relying on a focus
  that, for a region taller than the screen, scrolled nowhere at all. And below the phone
  breakpoint the bar stops sticking: three rows of chrome in a third of the width cost 29%
  of every screenful, and it is still the first thing on the page, one flick of a thumb or
  one press of Home away.
- "Jump to a team or player" now waits to be asked. The list navigated the moment its
  value changed, and a closed dropdown commits each option as you arrow onto it — so a
  keyboard reader heading for the twentieth player was taken to the nineteen before it on
  the way, each one a re-render, a scroll back to the top and a history entry, with the
  page they were reading gone before they had finished choosing. A mouse sees the popup and
  commits once, which is why nobody noticed. The list now only chooses; a Go button beside
  it does the going, Enter in the list does the same without leaving it, and the button is
  greyed out until there is somewhere to go. One extra press with a mouse, and the first
  press that does what it says for everybody else.
- A table too wide for the screen can now be reached from a keyboard, and says what it
  is. Thirteen tables scroll sideways on a narrow window rather than pushing the page out,
  and the box that does the scrolling is a plain `<div>` — nothing to focus, so the arrow
  keys had nothing to act on and every column past the right edge was unreachable, with
  nothing on the page saying it was there. A mouse or a finger never met it. Each one is
  now a tab stop, and named in the same breath: a focusable box with no name is announced
  as a box, so it borrows the chart's own heading or the table's own caption — words
  already on the screen, so there is nothing to keep in step — and is marked as a region,
  which puts it in the list a screen reader can jump between. Only while the table really
  is too wide: a tab stop in front of a table that fits is a keystroke that does nothing,
  and there would be thirteen of them on a desktop, paid for by exactly the reader this is
  for. So it is measured after each render, on resize, and when a chart's values table is
  revealed.
- Every table now says in print what its abbreviated column headings stand for. A ladder's
  `P W L D`, a squad's `GF GA Diff`, a players table's `S W-L` and `D rating`: 41 of those
  headings said nothing anywhere, and the ones that did say something said it in a hover —
  which is nothing on a phone, nothing on the printout pinned to the clubhouse wall, and
  nothing to a keyboard reader on the one table whose headings are sort buttons, because
  the tab stop is the button inside the cell and the words were on the cell. Each table's
  caption now carries a key under the sentence it already had — `GF — Games for · GA —
  Games against · Diff — Games differential` — built from the same strings the hover
  shows, so there is one wording to keep right rather than two; the ladder's hand-written
  copy of that explanation is gone, and the head-to-head grid, which builds its own
  headings, spells out each team's abbreviated name the same way. A heading that is
  already its own explanation stays out of the key — a club whose team names are short
  gains nothing from "Gnats — Gnats" — and a heading a table repeats, like the two `G`
  columns either side of a match card's net, is explained once, so the key stays a key.
- A printed sheet now says which competition, club and season it belongs to, and keeps
  every column. A season ends up as paper pinned beside the courts, and the paper was
  anonymous: the bar carrying the competition, the club, the season and the build stamp was
  hidden for print, so four of the five views printed nothing naming any of them and the
  club name appeared on no page at all. It prints now, and what goes in its place is what a
  sheet cannot use — the view tabs, the jump-to lists, the theme button, the skip link, the
  charts' Values buttons, the tooltip. The columns were the other half: wide tables scroll
  sideways on screen so a fifteen-column table fits a phone, and paper has no sideways
  scrollbar, so the right-hand end was simply cut off with nothing saying so. Measured at
  A4 on a twelve-team season, twenty columns were missing across five pages — nine of the
  head-to-head grid's fifteen, six of the players table's, three of a player's match
  history, the ladder's `Form` — and none are now. The cost is that a few long headings
  break mid-word where a page is tight, which is what the caption key above each table is
  for; a table that already fits is unchanged.
- A screen reader can now find the three caveats that sit above the numbers: which scoring
  rules were assumed, whether the season validated and off how many scorecards, and which
  scorecards could not be read. Each is now a named region, so it is offered in the list of
  places a reader can jump to. Two of the three were plain containers, which meant the
  label already written on the data-health strip was one the specification tells a browser
  to ignore — and there was no other way to reach any of them, since the page has no
  headings above its content and the skip link deliberately steps over them. Measured
  against the browser's own accessibility tree: eight regions on the page where there were
  six, nine where there were seven if a scorecard was refused, and nothing on the page
  moves by a pixel. A strip with nothing in it stays out of that list rather than becoming
  a signpost with no writing on it.
- A match that counts for nothing no longer reads like a result. A night called off partway
  can still carry a scoreline — the rubbers that finished before the rain are printed on the
  card — and in the match list that scoreline was the only emphasised thing in the row,
  because there is no winner for it to share the emphasis with. So the one row whose figures
  meant nothing was the row where the figures stood out most. Such a row now leads with the
  reason instead of ending with it — `cancelled — counts for nothing`, ahead of the sets and
  the points — and its scoreline is set in the same quiet weight as the text around it. Open
  the card and it says so in full: nothing on it is counted anywhere, not a game, not a set,
  not an appearance. That claim was checked by building the same season with the card and
  without it — the ladder, the head-to-head grid and every player's figures come out
  identical — and the sentence ends with whatever the competition's own rules say an unplayed
  round is worth, in the same words as the rules banner at the top of the page, so a club
  that switched preset gets a card that agrees with itself. A bye's row now takes its wording
  from that same place rather than from a second copy kept beside it.
- The demo season's washed-out round now shows the case worth seeing. It used to write a card
  with nothing on it — four rubbers, no games, nobody's night — which is the harmless shape
  of a washout and shows a reader neither the problem nor the fix. It now writes two rubbers
  finished and the rest abandoned, so a fresh clone shows a card carrying `12–5` and two sets
  to none on a night that counted in nothing at all. It can never show more than that: the
  number of finished rubbers is capped below whatever the format needs to decide a match, for
  every format the tool ships, because a cancelled card with a winner ticked on it would be
  an invented case rather than a demonstrated one.
- The Share column now accounts for what it does not add up to. That column is each player's
  share of the games their team won, and it can come to less than the whole: a rubber whose
  name cell was left blank has half a rubber belonging to nobody, and a player borrowed from
  another squad has no row in their opponent's table. Nothing is guessed in either case —
  putting somebody's name against a season they were not registered for would be worse than
  a column that falls short — but until now nothing said so either, so a reader adding the
  column up on the demo found 86.4% and no way to tell a decision from a broken sum. The
  table's caption now states the shortfall in games and as a percentage, and then names the
  cause it found, one clause per cause and a count of rubbers rather than of names. It stays
  quiet on a team whose column is complete, including one in the demo whose three cells
  round to 99.9%, because the test is the games and not the printed percentages. And it only
  points a reader at the data health section when there is a rubber listed there to find:
  where the whole shortfall is a night two scorecards named differently, the caption gives
  the total and stops, because that case is listed as a pair of files rather than as a rubber.
- The sentence about a night credited to nobody now counts nights rather than scorecards. A
  disputed night is two cards of the same fixture, so a team page could report "2 nights are
  missing from every row … two cards for them" about a single Tuesday.
- The dashboard no longer scrolls sideways on a phone. The strip that states which scoring
  preset, set rule and match format produced the page held each of its six lines unbreakable,
  and the longest of them is 933px, so the document was 945px wide behind a 320, 360 or 390px
  screen — and 953px behind 700. Nothing on the page looked wrong, because the content column
  was the right width all along; what the reader got was a horizontal scrollbar on the whole
  document, 585px of empty space to pan through, five of six lines of that strip cut mid-word,
  and the content off-centre for as long as they had panned. Those lines now wrap, and the
  document is exactly as wide as the screen at 320, 360, 390, 700 and 1280. It is
  `overflow-wrap: anywhere` rather than `break-word` because a set label from somebody's own
  scorecard can be one unbroken 53-character word, and only `anywhere` lets a box be narrower
  than its longest word; measured, `break-word` still left the document 382px at 320.
  Unconditional rather than behind a phone breakpoint, because 700 overflowed too. The strip
  is taller as a result — 138px to 250px on a 360px screen — which is recorded as the next
  thing to fix and not paid for by hiding it, since it is the only place the page says which
  preset its numbers came from.
- The progress meter beside the hero figure no longer refuses to be narrower than 260px. At
  280px — a folding phone's cover screen — it was laid out 260px wide inside a 256px card and
  hung 21px out of it. `flex: 1 1 260px` already asks for that width and yields when there is
  not room, which is the case the floor got wrong; nothing changes at any width where there is.
- The dashboard's data file is read by the browser's JSON parser instead of its JavaScript
  one, and opens about 3ms sooner on a desktop and perhaps 15ms sooner on a phone. It used
  to be a JavaScript object literal, which means the parser had to weigh at every one of
  its braces whether it was looking at an object, a block, a function body or something
  else; it is now that same season as a string handed to `JSON.parse`, which has none of
  that to consider. Measured on a real eight-team season with the file inlined so the read
  is not in the number: 10.1ms before, 6.8ms after, and the gap grows with the size of the
  season rather than being a fixed cost. The file is 15% larger on disk and 376 bytes larger
  once compressed, which is the price of escaping the season into a string — paid
  deliberately, because it means the escaping is the standard library's work rather than
  this project's, and a hand-written escape that gets one character wrong is a blank
  dashboard with no message.
- A club name or player name containing a character pasted out of a word processor no longer
  risks a blank dashboard on an older browser. Two invisible characters — the Unicode line
  and paragraph separators, which a copy-paste out of a word processor carries and which
  `config.toml` will happily hold — used to travel into the dashboard's data file as
  themselves, and a browser older than 2019 treats one of those as the end of a line and
  refuses to read the rest of the file. They are now escaped. Verified end to end on a club
  whose name and four of whose players are spelt with an apostrophe, an accent, quotation
  marks, a backslash and both separators: every one of them reaches the page as typed.
- Printing a page now does what the page looks like it will do, and the README says so.
  The stylesheet contained a rule claiming that every match card would show its set scores
  on paper whether or not the reader had opened it; it never worked, because a closed card
  hides its contents by a mechanism that rule could not reach, so no printed sheet of any
  page had ever carried a set table. Measured before changing anything, with every route
  printed to PDF and read back: 28 sheets, no set tables. Making it work was possible in
  one line and was measured too — it turns the matches page from 5 printed sheets into 30
  — so the promise is dropped instead of kept. Match cards now print as the reader left
  them: open a night before printing and that night's sets come with it; leave them closed
  and you get the one-line results. A card you opened is also kept whole rather than split
  across the fold, which was already true and is now measured (14 of 56 cards split without
  it) and tested.
- Every sheet of a printout now says whose season it is, not just the first one. The bar
  across the top of the page prints, so sheet one carried the club, the competition and the
  season and sheets two onwards carried nothing: measured across the five pages, 23 of 28
  sheets were tables of numbers with nothing on them saying whose. Repeating that bar down
  the page is not something a browser will do — every way of asking was measured, and the
  best of them prints the bar on top of the table — but the header the browser puts on each
  sheet itself repeats the page's name, so that is where the identity went. Each page now
  names itself as `Matches · Wednesday Division 3 · YOUR CLUB · Season 2`, which is also
  what the browser tab says, and 28 of 28 sheets now carry it at no extra paper. The header
  is the **Headers and footers** box in the print dialog, ticked by default; the README says
  so, because unticking it takes the name off every sheet but the first.
- With JavaScript switched off, the page's one note now sits where the content goes. Every
  number on a dashboard is drawn by a script, so with scripting off the note explaining that
  is not a caveat on the page: it is the page. It used to sit above the content area, which
  meant a reader moving through the page by region — or pressing the page's own "Skip to
  content" — was sent to an empty box and offered nothing, while a reader going top to bottom
  read the note and never knew. It is now inside the content area, and looks like the blocks
  of content that appear there when a script does run. Nothing changed for anybody with
  JavaScript on.
- Open a bye, a washout or a week called off, and the rubbers inside it are as quiet as the
  row was. The winning games in each finished rubber were still printed in bold, which left
  them the loudest figures in a card that twice says the night is counted nowhere. They keep
  their mark and their colour — somebody did win that rubber, and the scorecard says so — and
  lose only the weight. Every name on the card is still a link to the player, and the Status
  column still reports the rubber rather than the night; the night is already stated directly
  above and below the table.
- A team's "Points earned by round" keeps the round nobody played and says so. It used to
  print `not scored` there in the heaviest weight and brightest ink on the chart — the round
  that reported nothing carried its loudest row — and say it three different ways to the eye,
  to a screen reader and in the Values table. The row now reads "not played" in all three,
  quietened like the bar it has none of, and a line under the plot says what the round was
  worth in your competition's own terms: nothing at all under one scoring preset, the team's
  own average under the other. Numbers on any chart's greyed-out rows are greyed with them.
- The sentence under the Share column now divides by the number printed beside it. That caption
  says how many of a team's games belong to no row in the table, and the total it quoted was
  counted over a slightly wider list than the team's own Games column: every night of the
  regular season, including any the ladder could not price. In a competition whose nights
  cannot end level, that made the caption read "9 of the 37" under a row saying the team won
  18 — a column adding to 205%, in a sentence whose only job is to explain why it adds to less
  than 100. Both figures come from the same list now, so the caption and the table it sits in
  cannot disagree, and a night the ladder leaves out still counts in full in each player's own
  record, exactly as a final does. Nothing changed for a competition where a night can end
  level, which includes the demo: the same caption to the character.
- That shortfall is now worked out once, in the same pass that fills the column in. The page
  used to re-derive it in the browser — adding up the rows it had just printed, then reading
  the team's scorecards a second time to work out why, which meant deciding all over again
  which nights counted, which were finals, how many players a rubber holds and who had been
  borrowed from another squad. Those answers are written into the season file beside the column
  now and the page just prints them, so the caption and the data health section it points at
  have one source between them rather than two that can drift. Two consequences worth knowing:
  a night whose two scorecards disagree about who played is named by that caption only when it
  is a night the column covers, so a disputed final is now reported on its own card, in the
  round note and in the data health section but not there; and a dashboard built by an earlier
  version still opens, showing the short caption where the file has no shortfall recorded in it.
- The same caption now says when the Share column adds up to *more* than 100%. A scorecard with
  more names on a rubber than there are places to play it credits every one of them with the
  whole rubber, so the table can hold more games than the team won: a singles rubber with two
  names typed into it took one team's column to 106.4% with nothing on the page mentioning it.
  It now reads "The Share column runs 6.4% past 100%: the rows above are credited with 6 games
  more than the 94 the team won", and rubbers with a name too many are counted and named beside
  the two kinds already there. The games are left where the scorecard put them rather than
  divided among the extra names, because the marker on the card says how many places the rubber
  has and the names are what somebody typed.
- The reasons the column does not add up are now listed whether or not the games come out even.
  They used to be printed inside the sentence about the shortfall, so a season whose faults ran
  both ways — two rubbers a name short and one a name too many — cancelled to exactly 100.0% and
  the page said nothing about any of the three, even though the data health section lists them
  all. The count of each kind is kept separately from the one signed total, so a column that
  balances can still tell you three scorecards need looking at.
- The strip stating which rules produced the page now names them on the screen and explains
  them behind one tap, which takes it from 250px of a 360px phone to 115px. Wrapping those
  lines was what stopped the document being wider than the screen, and it cost height: six
  lines of 38, 19, 38, 38, 38 and 56 pixels, four of them wrapping because of the
  *explanation* beside the name rather than the name itself. The five names —
  `Scoring: games-bonus`, `Sets: first-to-6` and the rest — are what a club recognises its
  own competition by, so they stay where they are, one line each; what each one means, and
  the sentence naming the five settings in `config.toml`, moved into a single *What these
  mean* disclosure. On a 360px screen that is 18% of the phone instead of 39%, and 141px of
  the dashboard visible without scrolling where there were 5. Printing is unaffected: a
  closed disclosure normally prints nothing, so the printed sheet opens this one and drops
  the instruction to tap it — measured on paper, every explanation and the sentence about
  `config.toml` appear on a printout exactly once and the words *What these mean* on none of
  it. The strip is never hidden at any width, because it is the only place the page says
  which preset its numbers came from.
- Every table on the dashboard can be taken off it. Each one's caption carries a **Copy**
  button, which puts the table on the clipboard ready to paste into a spreadsheet, and a
  **CSV** button, which downloads it as a file named after the competition and the table —
  `wednesday-division-3-ladder.csv`. Thirty-four tables across the five views have the pair;
  the set rows inside a single match card do not, because the matches page puts all of them
  in one table (below). What leaves the page is read off the page rather than rebuilt from the
  data behind it, so the file holds the columns the reader is looking at, in the order they
  are looking at them. Two things are fixed on the way out, because a spreadsheet reads them
  differently from a browser: the dash this page prints for "no value" becomes an empty cell,
  and the true minus sign in a games differential becomes an ordinary hyphen, so a column of
  them still adds up instead of arriving as text. A cell a spreadsheet would run as a formula
  arrives as text too. The clipboard gets tab-separated columns and the file gets commas,
  which is what each of the two is actually read by, and the file opens with a byte-order
  mark so a name like *Zoë* survives the trip. The buttons float to the left of the caption
  rather than the right, which is not the conventional side: measured at 320px, a caption is
  as wide as its table and not as wide as the screen, so floating right put the pair beyond
  the right edge on 18 of 20 tables. If a browser refuses the clipboard the page says so and
  points at the CSV button beside it, and either button announces what it did to a screen
  reader. Neither prints.
- Charts on a page there are many of now export under the name of that page. The values
  table under a team's "Strength by set slot", and the two under a player's charts, were
  named after the chart alone, so every team's file was `…-strength-by-set-slot.csv` and a
  second download arrived as an indistinguishable copy of the first.
- Every match has a page and a link of its own, so one night can be sent to somebody without
  sending the whole dashboard. The address is the match id Match Centre printed on the
  scorecard — `…/index.html#/match/4200003` — so it survives a rebuild and the next round
  landing. The page names the two teams, links both of their seasons, says which round and
  which id it is, opens the card, and leads back to the matches list. Every card carries the
  link in its body rather than in the row that opens it: measured, a link beside the score is
  read out as part of that row's own name and adds a focus stop to every one of 56 rows. A
  card that printed no id says so where the id would go, instead of `match #` with nothing
  after it. Links do not print — on paper a fragment is a sentence nobody can follow.
- Fixed: a fragment with a broken escape in it, such as `#/team/%`, left the dashboard blank.
  A truncated or hand-typed link now reaches the same "not found" page as any other address
  that names nothing.
- A fixture in the declared draw that no scorecard was ever read for is reported, as
  `card-missing` under Data health, and its row on the matches page says **no card yet**
  instead of "scheduled". Three of Tuesday's four cards arriving is the ordinary case, and
  until now the fourth was described in the same words as a round in November: the row said
  "scheduled" for the rest of the season and nothing else mentioned it. Whether the season has
  got past a round is decided by other cards and never by a date, so a build replayed next
  year finds what it found on the night, and a club that types its whole draw in during August
  is not shown a dashboard full of faults. The finding shows what it is reading — how many of
  that round's fixtures do have a card, or which later round has one — rather than asserting
  the conclusion, and it says nothing about *why* the card is absent, because a forfeit, a
  washout and a card nobody exported look identical from here and the answer is written on the
  scorecard being asked for. It is a warning, so a build in the middle of a round still
  succeeds, and it says both ways to clear it: export the card from Match Centre into that
  round's folder, or run `tools/doctor.py` on it if the file is already there. Only a draw the
  club declared is checked; a draw worked out from the cards cannot be short of one. A card
  the draw has home and away the other way round is already reported as `draw-reversed` and is
  not also called missing.
- A round heading no longer says "not played yet" over rows holding that round's own
  scorecards. A round with cards missing says **no cards yet**, in the same colour as the
  fault it is, and a round nobody has reached still says "not played yet".
- Fixed: a fixture row for a round declared without a date printed "scheduled ·" and stopped,
  with nothing after the separator.
- `tools/doctor.py`: explains what a PDF is and why it did or did not read as a
  scorecard — metadata, font encodings, rectangle census, column rules, row bands,
  tick-box census, header fields, and a plain-language verdict. Exits nonzero when
  a page does not read, so it can be looped over a folder.
- `tools/doctor.py --redact`: the same report with every name, team, club, venue,
  date, path and rating replaced by its shape — `Priya Halloran` becomes
  `<2 words, 15 chars>`. A card that will not read is diagnosed from the page's
  measurements, and none of those name anybody; without this flag the only way to
  send them to a maintainer is to attach a PDF that names both teams, every player
  who took a court and both captains. The geometry, the scores, the counts, the
  cross-check verdicts and the weekday are all kept, because a report nobody can
  act on is not a support route.
- Every path `tools/doctor.py` is given comes back as a sentence, never a traceback.
  A build names this command beside each card it refused, so it is typed when
  something is already wrong: a file the account cannot read says so and says the
  build will refuse it too, a directory is told it is a directory and shown the
  command that would work, a symbolic link pointing at nothing suggests the share may
  not be mounted, and a path with nothing at it says just that. All of them exit
  nonzero, and one bad path does not stop the files named after it. A failure inside
  the reader itself names the kind of failure and asks for a bug report rather than
  blaming the card; under `--redact` the message is withheld, since it can quote the
  page, and the report says which flag would show it.
- `tools/demo.py`: writes a fictional club's season of scorecard PDFs, so the tool
  can be tried end to end without any real data. Deterministic for a given seed.
  `--clean` removes the cards the demo itself wrote and nothing else: it recognises
  them by the fictional club's letterhead printed on the page, so `--out` can be
  pointed at the same folder as a club's real exports. Any scorecard it did not write
  is listed as kept and left where it is, as is a PDF it cannot read and anything that
  is not a PDF. The folder is only named as safe to delete where emptying it would
  cost nothing.
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
- A version stamped on everything the build produces: the report's first line, the
  `meta.version` field of `season.json`, the embedded data island, the front page's
  footer and each competition page's footer. The number has one home,
  `matchcentre.__version__`, and the suite fails if any of those five places stops
  quoting it, if the changelog's newest release disagrees with it, or if an example
  of the report line in the documentation goes stale. A run that fails still prints
  the stamp, so a report pasted into an issue always says which build produced it.
- `tests/test_hostile_input.py`: a deterministic fuzz suite over damaged PDFs —
  truncated files, corrupted cross-reference tables, cyclic object references,
  absurd numbers, and bytes that are not a PDF at all. The promise it holds is that
  the reader refuses a page and names it, rather than raising; found and fixed one
  case where a malformed folder made `build.py` print a traceback instead of an
  explanation.
- `tests/test_doctor.py`: holds `--redact` to both halves of its promise against a
  card whose every value is written in the test file, so the check knows what was
  on the page independently of what the redactor thought it learned.
- `tools/screenshots.py`: regenerates the images in `docs/screenshots/` from the
  demo season using a headless browser. A maintainer tool, not a dependency — it
  shells out rather than importing anything, so the no-dependency rule still holds
  and the suite still passes on a machine with no browser at all.
- GitHub Actions in `.github/workflows/`: `tests.yml` runs the suite on Python
  3.11, 3.12 and 3.13 with no install step of any kind, which is itself the point —
  a workflow that needed `pip install` would mean the no-dependency claim had
  quietly stopped being true. `demo.yml` builds the demo season and publishes it to
  GitHub Pages, so a club can see a working dashboard before deciding to export
  anything.
- Issue forms and a pull-request template. The one for a wrong number opens by
  asking which scoring scheme is in force, because a ladder computed under the
  wrong scheme is not an error — it is a plausible, wrong table, which is worse.
  Both ask for a redacted report rather than a file.
- `CONTRIBUTING.md`: the two rules that are not negotiable (no dependencies, no
  real people in the repository), how to run and add a test, and what to do when a
  change alters what a number means.
- `SECURITY.md`, and a private route for the one bug that must not be reported in
  public. `--redact` exists so that a club can paste a diagnostic report into an
  issue without publishing its membership, so a redacted report containing a real
  name is the most serious fault this project can have — and until now the only
  channel offered for it was the public tracker, where saying which line leaked
  republishes the leak. The policy says what counts (a leak, a card that costs
  unbounded memory or escapes the folder it was pointed at, a name that becomes
  live markup in the published page), what is out of scope, and how to reproduce a
  leak on an invented card with `matchcentre/cardwriter.py` rather than a real one.
  It names no URL on purpose: the file travels into every fork, and a link would
  send a club's leak to somebody else's maintainer. Every redacted report now ends
  by pointing at it, printed after the scrub so that a card naming a team after the
  policy cannot shape the one line that says what to do about it.
- `--version` on all four commands, and a written promise about what the number
  means. The line is the one every report starts with —
  `matchcentre-dashboard 0.1.0  (python 3.12.3 on linux)` — printed on its own, with
  no cards, no `config.toml`, no browser and nothing written, so a folder nobody has
  run yet can still say what it is. One function builds it, so the four parsers
  cannot describe the same build four ways. What moving each of the three numbers
  promises about a club's own `config.toml`, `data/` folder, commands and published
  figures is in the README under *Versions and upgrading*, together with how to do
  an upgrade and why nothing has to be migrated; `CONTRIBUTING.md` has the release
  steps. A corrected reading is a PATCH and is called out as the one bump that can
  move a ladder somebody has already sent round.
- The two refusals that happen before any card is read now print the version too.
  `no such directory: data` and `config.toml already exists` are the failures a club
  is likeliest to meet on a first evening, and both returned without flushing the
  report, so neither said which build refused.
- Every run now names the dashboards under `site/` that it did not build, and
  `--prune` deletes them. A competition directory is a complete self-contained copy
  of the dashboard — which is what lets a club zip one up and email it — and nothing
  had ever looked at what was under `site/` that a run did not write. So three
  ordinary things left a finished, openable, wrong dashboard on disk and said
  nothing: deleting `data/demo-season/`, exactly as the README's own quick start
  instructs; archiving a division that finished; and turning multi-season mode on,
  which moves every competition one level deeper and so orphans an entire site in a
  single run. The front page stops linking them, which is the trap and not the fix —
  the folder is then invisible to everyone except whoever bookmarked it or was sent
  it, and it opens by double-click showing a ladder from the day it was written. The
  report names each one with the figures out of that stale copy's own `season.json`,
  since this run has no cards for it and nothing else is left that knows what it is,
  and says which of the three reasons applies.
- `--prune`, for the other half of that answer. Nothing is deleted without it: a
  club that published a finished division on purpose is indistinguishable from one
  that has just lost a folder, so the run reports and the club decides. What it
  deletes is decided on the marker line this tool writes into `season-data.js` and
  not on a folder holding files with the right names, because a club's own page in a
  directory named after a division looks identical from the outside. Any directory
  holding one file this tool did not write is named and left exactly as it is —
  whole, not emptied around the file it was refused for — and the run exits 1 so a
  weekly script notices. It deletes nothing at all on a run that read no cards,
  which is the combination that could otherwise empty a published site because a
  drive failed to mount, and it refuses to be combined with `--check`, which
  promises that nothing is written. An emptied `site/<season>/` goes with the last
  dashboard in it; one still holding a division being played does not.
- A competition declared in `config.toml` with no scorecards under it yet is listed
  on the front page, greyed and not a link, reading **No results yet** and
  **declared in config.toml**. It was listed nowhere at all before: the page is built
  from what the run produced, so a club typing next season's division in ahead of its
  first night saw no change, and a competition this tool has never heard of and one
  whose first night has not happened look the same from outside — only one of them is
  worth waiting for. The card becomes a link on the run that reads its first card,
  and it is the same entry rather than a second one, because the section's own name
  is the id its cards are filed under. It says where it came from rather than what it
  is waiting for, because the other thing that puts a card there is a `division` spelt
  differently from the card's, which is a competition the club does not run — so the
  card names the file to fix instead of promising scorecards that are not coming. A
  section naming a division but no day is not listed and never was: several
  competitions match it, and putting it on the page would mean choosing a night for
  it. It is reported on the terminal exactly as before, with one sentence added
  saying it is on the front page.
- `[namesakes]` in `config.toml`, for two different people who share a printed
  name, and `player-two-rosters`, the warning that finds them. Nothing looked for
  this case before and nothing could be done about it: the player index is keyed on
  the name a card prints, so two unrelated M Kellys were one row whose sets add up,
  whose win rate is neither of theirs, and whose team, rating and captain's mark
  belong to whichever roster was read last. The check reads the *roster blocks* —
  the registered squads — because a fill-in is printed in the set rows of the side
  that borrowed them and in nobody's roster, so one name on two rosters is two
  registrations. It is also the only thing that can see the case at all: two
  reserves who are named every week and never play leave every set-level check,
  every total and the ladder untouched. A warning, and it stops at saying so: two
  rosters carrying one name is equally one person who changed teams mid-season, the
  cards cannot tell those apart, and splitting a transfer would invent a person who
  never existed — the same reason two team banners that look alike are reported and
  never merged. What it does instead is print the line to paste, naming both teams
  as the evidence the sentence rests on, and `--discover` writes that line into the
  config it renders, commented out, so the club's job is the decision and not the
  typing. The two teams are always teams of one grade: each grade gets a dashboard
  of its own, so a name printed in two grades is two players already. An entry is
  keyed on the name, so if the same name is merged in two grades the offered line
  names all four teams and fixes both. And if the two of them ever played on the
  same night the merged row is already an error — `player-two-teams`, nobody is on
  two courts at once — so the one line clears the warning and the errors together.
- A `[namesakes]` entry that separated nobody is reported rather than passing
  silently. The split works by rewriting a slug, so an entry matching no card
  rewrites nothing, and a dashboard built from a line that did nothing looks
  exactly like one built from a line that worked. Three ways to write one: a name
  no card prints, and a team spelt differently from its banner — both a keystroke
  from the line the report offers to paste — and one team each in two different
  competitions, which matches every team named and still separates nobody, because
  a merge only ever happens inside one competition. Everyone who reached a set row
  counts and not only the registered squads, since a fill-in is in no roster block
  and judging the entry on rosters alone would report a split that had in fact
  taken effect.
- A `config.toml` written before you export anything is no longer a dead end. A run
  on an empty `data/` writes the whole file with every optional setting commented
  out, and it now says in its first sentence that no scorecard went into it and that
  the dashboard does not wait for it. Once the PDFs arrive, a plain run builds the
  dashboard and leaves your file exactly as it is — which is why your edits are safe
  — so instead of asking you to type in what it worked out, the review section names
  the command that writes it for you: `python3 build.py --discover --force`, carrying
  whichever `--data`, `--out` and `--config` this run was given. It is offered only
  while the file describes no competition, the one state where a rewrite has nothing
  of yours to lose. A run that read cards and could build nothing from them no longer
  says none was found, either: it names the count and points at the refusals above.
- `--discover --force` keeps a declared `format`, `slots` and `min_sets_for_result`.
  They were written back commented, which unsets them, so a rewrite quietly loosened
  the check a club had asked for: `format` is what makes a card with a row missed an
  error rather than a card of a different shape, and the next run's rules section
  read `format  read from each card` with nothing saying why. Undeclared, the three
  stay commented as before — a shape written live on a first run would pin every
  later card to the first card's shape.
- Run from anywhere but the project, the tool says where its own cards are. `data/`
  and `site/` are found next to wherever you are standing — deliberately, so that two
  copies of this project cannot write into one `site/` — which meant that `python3
  /somewhere/matchcentre-dashboard/build.py` from a home directory answered `no such
  directory: data` and then advised copying in the PDFs you exported last week and
  already have. It now names the folder it looked in, the `data/` beside `build.py`,
  how many scorecard PDFs are waiting in it, and one line to paste: `cd
  /somewhere/matchcentre-dashboard && python3 build.py`, carrying the folders this run
  was given and quoted if the path has a space in it. A run whose own `data/` exists
  and is empty gets the same paragraph at the end of its report. Nothing is said when
  there is nowhere to send you: a folder named in full, a project whose `data/` is
  empty too, or the ordinary case of standing in the project already.
- `tools/demo.py` prints a command that runs from where you ran it. It writes its
  cards next to `build.py` whatever directory it is run from, and then printed `next:
  python3 build.py` — which from anywhere else cannot find `build.py`, and on a
  machine with two checkouts finds the wrong one. The `cd` is now in front of it, and
  only when there is one to add.
- The season you keep is no longer the season you are told to delete. `seasons` under
  `[build]` is the mode — one ladder per season, or the whole archive on one — and
  `[seasons]` is a table of folder labels. They are one word apart, and three ways of
  reaching for the mode did nothing quietly: `seasons = "multi"` written at the top of
  the file, `mode = "multi"` written inside `[seasons]`, and a table of correct labels
  while `[build]` still said `single`. Each now names the line to write, and nothing is
  read as the mode from a line that is not it — the labels you typed are kept exactly
  as typed. `config.example.toml` was shipping the third of them, and its labels are
  now commented out.
- A round built from cards played in two different calendar years now says so, before
  it becomes an error. Keep last season's exports beside this season's in
  single-season mode and last season's round 1 is filed onto this season's: the report
  names the rounds, in draw order, says the folder holds more than one season, and
  gives the one line that separates them. If it is one season that ran across New Year
  — a summer competition, or a round washed out in December and replayed in January —
  it says that is right and there is nothing to do, because the check reads only the
  cards that count as played.
- The findings about two cards for one fixture, and about a team down to play twice in
  a round, no longer ask for a file to be deleted when the cards were played in
  different years. All four sentences that did — "Delete the spare file from data/",
  "…comes back when the card that is wrong leaves data/", "remove the file that is
  wrong from data/", and the one about points counted twice — are replaced by the
  reason and the setting, so following the advice cannot cost a club a season to fix a
  round. Where the two cards really are one night exported twice, each says what it
  always did.
- A rebuild writes only the files whose contents change, and the date on a page is when
  its figures last changed. Of the eight files a one-competition build writes, four held
  the same bytes run after run and four differed only in the date stamped into them, so
  every rebuild rewrote all eight and moved every page's date to the day the command was
  typed — a footer reading `25 Sep, 8:00am` under figures that had not moved since the
  24th, and the whole of `site/` reported as changed to anything watching the folder: a
  sync, a `git status`, a publish step. A competition whose figures are unchanged now
  keeps the date it had, the front page takes the newest date of the competitions it
  lists, and no file is written with bytes it already holds.
- The last line of the report says which of those files it wrote. Three sentences, not
  one count: `wrote 8 file(s) under site` for a run that wrote all of them, `wrote 4 of 8
  file(s) under site; the other 4 already held what this run would write` for a run that
  wrote some, and for a run that wrote none, `wrote nothing: all 8 file(s) under site
  already held what this run would write, so the dashboard still shows 24 Sep, 7:30am --
  the build its figures come from`. The date is there because `wrote nothing` on its own
  reads as a failure, and a club who has just copied a card in needs to know which build
  they are looking at.
- A run that finds a dashboard from an earlier build says what it is about to change, in
  a `since the last build` section: the fixtures that arrived with their figures, the
  fixtures whose figures moved with the old ones beside the new (`sets 4-0 -> 0-4, games
  24-7 -> 9-24, result Riverside Rockets -> Parkview Wanderers`), the fixtures that have
  gone, and the teams that changed places (`Lakeside Spinners 2nd -> 1st`). The file count
  answers "did that do anything" and could not answer "did it do what I meant": a run that
  lost one match, corrected another and gained a third printed `wrote 4 of 8 file(s) under
  site` and `exit 0: all 6 card(s) scored, no errors` — the same two sentences as the run
  before it, because a card arriving as another goes leaves every count where it was — and
  named none of the three. Capped at eight per kind with a line saying how many were left
  out, earliest round first, and silent on a first build and on a run that changed
  nothing. `build.py --check` prints the same list under `changes a build would make` and
  writes nothing, which makes it a preview of the whole build rather than of the file list.
- A fixture that has **gone** is printed at every verbosity, `--quiet` included, with the
  file the last build read it from. It is the one thing this program could not otherwise
  report: a card it cannot read is refused by name and counted in the exit status, but a
  card it was never given is invisible, and what it publishes is a complete-looking ladder
  one match short. Both causes are spelled out under the list, because their answers are
  opposite — a card removed because it had been exported twice is a tidy-up that worked,
  and a card nobody removed means a file that was in `data/` is not there now. The path
  named is the one the *last* build read, which is the only name left to go looking for.
  Troubleshooting entry: *The report says a fixture has gone*.
- `build.py --single-file` writes each competition a second way: one `.html` file, named
  after its folder, holding the whole dashboard — stylesheet, script and season folded
  in — for emailing to a team or opening on a phone. A competition's folder is five files
  that have to travel together, and the one anybody would think to send is `index.html`,
  which on its own renders a link to a front page that is not there, a placeholder where
  the competition's name goes, and a search box over nothing: 166 characters of text,
  measured in a browser. The folded page renders 4,683 and is 366,424 bytes — smaller
  than the folder it came from, because a folder holds the season twice and one file
  needs only the copy the browser reads. The report names it and its size (`one file
  site/wednesday-division-3/wednesday-division-3.html  (358KB)`), at every verbosity
  including `--quiet`, and says nothing at all on a run that folded nothing. Once a
  folder has one, every later run keeps it current without the flag, so the unattended
  Tuesday-night build does not leave a club emailing last week's ladder. On the page
  itself, the way back to a front page that is not there is removed rather than
  retargeted, the note for a reader with scripting off no longer names two files beside
  it, the season picker offers no way out of the file, and the footer reads `source:
  this file`. What makes such a page this tool's is a mark inside it and not its name,
  so a page of the same name a club wrote itself is left alone, and a stale folded
  dashboard is deleted by `--prune` along with the other five. README section: *Sending
  somebody the dashboard*.
- A rubber one side did not turn up for is credited to the side that did. The card says so
  with a `Forfeit` tick beside a row left at 0-0, and the tick was read and then dropped on
  the way into the result, so the set went to nobody. On a card whose two singles the away
  side conceded, with the doubles won 6-2 and lost 2-6, the home side was credited sets 1-1
  instead of 3-1 and the night was scored a draw instead of a win. The card had it right,
  and was reported twice, its TOTAL row as `totals-mismatch` and its verb as
  `verb-mismatch`, and the run exited 1. The drawn points lifted the side that lost from
  fourth to third, and each player who turned up had a rubber on their record that they
  neither won nor lost. Who a forfeited rubber goes to is now one function,
  `model.forfeit_set_winner`, which the model, the validator, the reader's own check of
  the TOTAL row and the card writer all ask: the ticked box first, and the games printed
  on the row only when both boxes are ticked.
- A night the other side sent nobody to is a match that was played. Every rubber ticked
  `Forfeit` at 0-0, with `Match Forfeited` left unticked, was `recorded as unplayed` and
  left out of the ladder, so both teams stood a match behind the rest of the grade (F10)
  and the side that turned up had nothing for it. It is now the 4-0 win the card prints,
  paid as played (4.0 and 2.0 under `games-bonus`), and it is not described as "level on
  games, decided on sets" over a night on which no game was played. Both boxes ticked on
  a 0-0 row award it to nobody, so a card of such rows is still a night nobody played.
- Two cards for one fixture that tick `Forfeit` on different rubbers disagree about the
  set rows, and are reported as the error that says so, with neither counted. Every total
  on the pair matches, so compared without the ticks they were a harmless repeat — counted
  once, under "every figure on the page is right" — although each gives the singles to a
  different side.
- A forfeited rubber whose row prints a finished set won by the side it ticks `Forfeit`
  against is reported, once, as `set-forfeit-contradicted`. The tick decides who is
  credited the set, so a card that ticked the box on the wrong side and totalled the
  rubber the way the tick says agreed with itself in every printed total, and nothing said
  a word while a set one side won on court was credited to the other. It is a warning and
  not an error, because a rubber awarded against the side that won it on court is
  recorded exactly this way. A side that forfeited while ahead in a set nobody finished
  is not reported.
- A card refused over its checkboxes says which half of a box is missing: the square,
  or the label that names it. A card whose tick-box labels are worded as another
  template words them — `Walkover` for `Forfeit`, `Match Won` for `Match Winner` —
  draws all 30 boxes and names none, and was refused with *the commonest hairline width
  on the page is 8.07pt, and no pair of edges that far apart forms a square* while the
  doctor counted 30 outlines above the verdict. It now counts the squares it found,
  lists the seven labels it looks for, and says what the page prints instead: the
  labels it prints away from their boxes, with the reach and the 1.5pt line tolerance
  a box has to meet, or the nearest words it prints (`'Forfeit:'`). The square sentence
  stays for a page whose edges make no square, and a page whose box edges are stroked
  rather than filled is told that, not that it paints none. The doctor gives the cause
  from the kind of refusal, one kind for each half, instead of its *stricter test*
  fallback. The reader's summary (`checkboxes: 30 squares found, 0 named by a label`)
  gives both counts, and a card whose labels name only some of its boxes is refused,
  as below. Troubleshooting entry: *A card was refused*.
- A page whose table is drawn in outlines rather than fills is refused for that, and no
  page is called "not a scorecard" any more for want of rules this reader can see. A
  fixture card with each `re f` rewritten to `re S` — one byte for one byte, all 200
  rects still where they were — was refused as `the page paints no table rules, so it
  has no set table and is not a scorecard`, while `tools/doctor.py` found its 8 column
  rules over 4 row bands and called its one outlined square a `ticked checkbox fill`.
  It now reads `the page paints its table rules as outlines rather than fills, which is
  not how Match Centre paints a card, so its set table is not read`, the detail counts
  what was outlined (`32 outlined rect(s) make 8 column rule(s) over 4 row band(s); 0
  of the page's 200 rects are filled`) and ends `export it again from Match Centre`,
  and the doctor calls that square a `box-sized square, not a tick: its inside is not
  painted`. Outlines are taken for a table the way filled rules are, as the largest run
  of line on the page and only with at least four rules, so the same sentence reaches a
  card whose table alone was outlined, which was refused before for a gap between its
  row bands, and never a rules sheet with one thin outlined bar. The page is still
  refused, on purpose: outlined, a tick is only the box it sits in drawn again, and
  rasterised, the inside of the box goes from 100% dark to 0%. A page with no rules at
  all is now told `no table rules could be found on the page, so no set table was
  read`, because the old sentence was as wrong about a card whose rules were drawn as
  stroked lines or as paths, which this reader does not see and which look, rendered,
  like the card they were drawn from. Its detail says that the page is either not a
  scorecard or one another program re-saved, and to export it again if it is one.
  Troubleshooting entry: *A card was refused*, two rows of its table.
- With more than one competition, the `one file` line counts the folded pages the run
  wrote, not every one it has. On three demo divisions it said `3 written, one beside
  each dashboard` under `wrote nothing` on a rebuild of the same cards, and said
  it again after one card was added, when one page had been rewritten and two left alone.
  It now takes the three shapes of the `wrote` line above it: `3 written`, `1 of 3
  written, one beside each dashboard (1075KB in all); the other 2 already held what this
  run would write`, and `none written: all 3, one beside each dashboard (1071KB in all),
  already held what this run would write`. Both lines get their shape from one function,
  each asking it with its own count, because the two counts part company: the second run
  of a first build rewrites every page under `11 of 21`, and a run that only puts back a
  deleted `index.html` rewrites none under `1 of 21`. The size is still the total of every
  folded page on disk, because the pages a run left alone are files a club would attach
  as well. One competition's line names the file and states no count, and is unchanged.
  README section: *Sending somebody the dashboard*.
- The same cards build the same bytes, and the test suite holds it to that: a
  one-season and a two-season project are each built in three folders, under three
  time-zone settings and three hash seeds, on a first build and again on the next one,
  and every file in the project, `config.toml` included, has to come out the same to
  the byte, with the report the same word for word. The test it replaces left each run
  of Python to choose its own hash seed, and let a doubles pair deliberately written in
  set order through on some runs and not on others. The `--generated` help no longer
  says the flag is what makes a rebuild of unchanged cards identical — a run over the
  same cards and the same `config.toml` as the run before it leaves every file as it
  was without it — and says what it is for instead: two fresh builds from the same
  cards and config writing the same bytes. The before-and-after recipe in
  `CONTRIBUTING.md` built `before` with no config and `after` over the one the first
  build wrote, which on its own changes `season.json`; it now builds `before` twice,
  and `diff -r` on unchanged code is clean.
- A published dashboard does not say where it was built. `season.json`, the page's copy
  of it and the one-file page name each card by the data folder's own name and where it
  sits inside it (`data/round-01/quolls-v-bunyips.pdf`), and `meta.source_dir` is that
  name, resolved, so `--data .` and a symlink publish what the folder is called. Built
  with `--data` spelt in full — the form the README gives for a cron entry — the demo
  season with four refused cards and one card exported twice carried the full path 44
  times in each of those three files: every match's source, every refused card's path
  and reason, both files of the duplicate and the problem list's messages, the
  operating system's own quoting of a name included, and with it the account name of
  whoever built it. Now it carries none of it. Six spellings of one folder, `data`,
  `./data`, the full path, a symlink to it, and `.` or the full path typed from inside
  it, publish the same bytes; four of the five that are not `data` used to write 3 of
  the 9 files differently. `--data data` publishes exactly what it did before, so a site
  built that way is left as it was by its first rebuild, and every path on the page is
  still one `tools/doctor.py` finds from the project folder. A site built with the full
  path takes a new stamp once, on the rebuild that takes the path out. Nothing else in
  the document changes: a season called `Summer 2026/27` whose cards are kept in `2026`
  is published as the club wrote it. The report still names every file by the path it
  was read from, and a fixture that has gone is named under this run's `--data`,
  whichever way the build before it was given the folder.
- A fill-ins heading that does not say `Fill-ins` ends the squads at the foot of a
  card instead of joining them, but for one wording, below, that reads like two
  players. The block used to end only at a word beginning `fill-in` or
  `comment`, so on a template that words its fill-ins heading any other way each
  team's copy of the heading, and every fill-in printed under it, joined that team's
  squad. Measured with every demo card saying `Reserves` for `Fill-ins`, at exit 0: 20
  players where there are 18, two of them called `Reserves (Home Team)` and `Reserves
  (Away Team)`; the demo's one fill-in, a Magpie who stood in once for the Rosellas,
  put on both squads, shown as a Rosella, and marked a fill-in on each of the four
  Magpies cards that put that player on court; no `fill-in` warning at all; and 35
  warnings where there are 6, one of them advising a `[namesakes]` entry that would
  have split that one player in two. Two things now end a squad besides those words.
  One is a line printed once for each team that reads the same except for `home` and
  `away`, and that season now builds the same site and report as the demo, byte for
  byte. The other is a line with no rating whose name has a bracket, a colon or a word
  with no letter in it, which is how a template that calls a side something else
  reads: `Emergencies (Visitors)`, `(Host)` and `(Guest)`, or the team's name after
  a dash. With every demo card's away heading saying `(Visitors)`, the season had the
  same 20 players and 35 warnings, and now has the demo's 18 and 6. Each card notes
  what it took for a heading, quoted, because a player printed unrated with a
  nickname in brackets looks the same and ends their squad there. Two players who
  share a printed name on one row of opposing squads are still two players, a player
  printed with no rating is still in the squad, and a heading printed word for word
  the same over both squads with none of those in it, `Emergencies` alone, is left
  to the entry on it below. Troubleshooting entry: *The report says a card's roster
  was not read*.
- A roster block with nothing under its squads to say where they end is not read, and
  the card says so, as a card with no `Players` heading already did. Such a block used
  to run to the foot of the page: measured with no fill-ins heading and no `Comments`,
  the comment `Rain stopped play at nine` became two players, `Rain stopped` and `play
  at nine`. The same page comes from a download that died part-way through the block,
  and of the 42 places to cut a card there, 14 read a wrong squad with no warning at
  all, three of them with a first name cut from its surname standing as a player. All
  42 now read neither squad. In the report, a card whose squads were not read is one
  `roster-missing` that names the side, or says neither, and says their ratings went
  with them, where it was one for each side and a `ratings-missing` besides; and the
  names on such a side that no card registers are one `unknown-player` for that side,
  not one for each set row. Measured on the test season with its first card cut in
  the away squad: before, 13 players, a first name cut from its surname among them,
  and 8 warnings, none of them saying the card was cut; now 12 players and the uncut
  season's one warning, with one `roster-missing` beside it. Measured on the demo
  printed with no fill-ins heading and no `Comments` on any card: 45 warnings, 14 of
  them `roster-missing` and 28 `unknown-player`, two for each played card, since no
  card registers anybody; before, 13 warnings and two players made from a card's
  comment. The cost is the one a card with no `Players` heading already pays: a
  player that card lists and none of its set rows name gets nothing from it. Cut
  after its last squad member, the one place a cut used to leave both squads whole,
  a demo card adds one `roster-missing` to the demo's 6 warnings and nothing else,
  since other cards register everybody on its set rows.
- A card's date is read in seven forms: `DD/MM/YYYY`, `DD-MM-YYYY`, `DD.MM.YYYY`,
  `DD/MM/YY`, `D Month YYYY`, `D Mon YYYY` and `YYYY-MM-DD`. Figures are read day first,
  a date impossible that way is read month first with a warning saying so, a two-digit
  year is 20YY, and a month in words may be cut to three letters or more in any case
  (`Sept`, `AUG`), with a full stop after it or a comma before the year (`18 Aug. 2026`,
  `18 August, 2026`). The reader knew the first and the last. `18 August 2026`, `18 Aug
  2026`, `18-08-2026`, `18.08.2026` and `18/08/26` each read as no date, with a warning
  that no date was printed above the set table, and a season printed `Tue 18 August 2026
  7:00 PM` was six cards found and none scored; it now scores all six. A form outside the
  list (`August 18, 2026`, `18th August 2026`, `2026/08/18`) is left unread rather than
  guessed at, because `05/08/2026` is a real date both ways round, and the warning names
  the forms that would have been read. A date in a listed form that is not a real one
  (`32/13/2026`, `2026-13-45`) is quoted as not a real date.
- The weekday on a card is the one it prints beside its date, not the date's own: the
  word in front of the date, or else the one straight after it (`18/08/2026 Wed 7:00
  PM`), in full or cut short, `Weds` included. A weekday word further off, such as a
  letterhead `SAT 1 TENNIS` printed on the date's line, is not read. Worked out from the
  date, the two could never disagree: a card printing `Wed 18/08/2026` read as a Tuesday
  and nothing said so, and a card whose date did not read had no weekday either, so the
  filing's fallback to the printed weekday never ran. A season printed `Tue August 18,
  2026 7:00 PM` was refused whole; it now scores all six, undated and filed by the
  Tuesday each card prints, with a note on each match saying so, and the lines on the
  dashboard that would have held the date leave it out rather than printing
  `Round 1 ·  · match #2000001`. A card printing `Wed` over a Tuesday's date, in front
  of it or after it, is filed by the date, with one note per card: six for the six-card
  season, where there were none. A card with neither is still refused, and the refusal
  names the forms a date is read in, because a refused card's own warnings are not
  printed and the old detail was `header date was ''`. `tools/doctor.py` finds a date in
  any of those forms and in the common ones the reader leaves alone, withholds every one
  of them under `--redact`, and its verdict names the date's weekday and any other the
  card prints (`2026-08-18 Tuesday (the card prints Wednesday; the date is what files
  it)`), asking the build's own filing rule which of the two files the card. README
  section: *A card was refused*.
- The matches page has a table of every rubber on its cards, behind an **All 60 rubbers in
  one table** button under the heading, with a **Copy** and a **CSV** of its own
  (`wednesday-division-3-all-rubbers.csv`). The set rows inside each match card have no
  buttons, and until now nothing else on that page had either: measured on the demo season,
  15 tables holding 60 set rows and none of them exporting, so who played which rubber all
  season was fifteen cards to open and retype. Each row is the row its card prints, with the
  date, round, match id and both teams in front of it and two columns after it that a row
  loses when it leaves its card: **Winner**, which the card shows only as a weight and a
  colour, and **Night**, which reads `Cancelled — counts for nothing` on every rubber of a
  washout. Who won is `season.json`'s answer and not the games', so a 5-4 rubber that was
  retired goes to nobody and a 0-0 rubber nobody came for goes to the side that did. The date
  is shown the long way and leaves as `2026-08-19`, which LibreOffice reads as a date where
  `Wed 19 Aug 2026` stays text. The table stays shut until asked for, because it more than
  doubles the page — 1585px to 3860px tall at 1280px wide — and it prints only when it is
  open: then as eleven more A4 sheets on top of the page's two, every row and column there
  but, as with any table wider than the paper, with words broken in every cell. A night two
  cards disagree about reads `Cards disagree — counts for nothing` there, on every rubber of
  each of its cards, as an entry below says. In a dashboard of several seasons
  every file a table downloads as now carries the season too: measured on five seasons of one
  division, all five ladders downloaded as `wednesday-division-3-ladder.csv`, and now each is
  `wednesday-division-3-round-01-ladder.csv` and on, from the season's folder.
- The ladder's Form column leaves the page as one letter per result with a space between,
  `L W W W W`, where it used to leave as `LWWWW`. The five badges have nothing between them,
  so Copy and CSV read them as one word, and LibreOffice's split on spaces made one column of
  `LLLDW` where it makes five of `L L L D W`. The letters were never ambiguous — a draw is D,
  a forfeit is the win or loss it was, and a bye or a washout has no letter at all — but the
  results could not be taken apart again. Before this change and the one above, walking every
  exporting table on every route of the demo season, 174 of them, and of a seven-team season
  with a bye every round and two draws, 202, the Form column was the only one whose file said
  anything the screen did not.
- Every colour the stylesheet paints now comes from its palette, and a test fails on a
  colour written in by hand anywhere but a palette's own tokens: in any property, as hex,
  as a colour function or as any of the 148 colours CSS names. Four were: the tints behind
  the win and loss badges, the tooltip's shadow, and a white page background restated for
  print. None of them looked wrong. Each badge's letter clears 4.98:1 on its tint over
  every surface it can sit on, and in a browser the page computes the same colours before
  and after in all twelve combinations of screen or print, OS light or dark, and the theme
  toggle. But no palette could reach them, so a theme that retuned its surfaces would have
  retuned everything except these, and nothing would have said so. The check that already
  existed read only the colour words are painted in, and a magenta win tint and a magenta
  tooltip shadow passed every stylesheet test. The tints are now `--win-wash` and
  `--loss-wash`, one value in every theme. Stepping them up for dark, the way the
  rejected-card wash is stepped, would put a win's letter at 4.46:1 on a hovered row,
  which is under AA, and would print the heavier tint on every page printed from dark
  when backgrounds are printed, because print is laid over the dark palette and does not
  restate it.
- A rest the draw states for a team that played that round is no longer paid, drawn or
  offered back. A card for the resting team already stopped the bye, but only a card the
  ladder counted: two cards that disagree (F9), or a level card under `draws = false`,
  are set aside before the byes are added, so the team was paid its average for a night
  it played. On the demo season, with a byes line resting Brindle Quolls in round one
  and a second, disagreeing card for their match that night, Quolls were counted 5 and
  given 26.0 points where the refused night leaves 4 and 20.8, beside a warning that
  said the card won and no bye was credited. With the one card, the Matches page drew
  `Brindle Quolls bye` under Quolls' own result, and the review block offered the round
  back for pasting in with `bye = "Brindle Quolls"` under the fixture that has them
  playing. Whether a rest stands is now one rule, read by the ladder, the page, the
  review block and the report, and judged against every card read, the ones the ladder
  sets aside included: it does not stand where the round's fixtures or a card have the
  team playing. The warning, `bye-contradicted`, names the team as the club spells it,
  no longer says `the cards draw rests` of a line the club typed, and says the card wins
  only where the ladder counts it. Beside a card the ladder set aside it says instead
  that the round is in none of the team's ladder figures.
- A round of a declared draw that rests a team and also lists it in a fixture is now
  said, as `bye-and-fixture`: an error once the round has cards, and a warning before
  then, when nothing is counted from it yet. It used to pay the rest: pasted in with
  Quolls' round-one card missing, that offered draw counted Quolls 5 and gave them 26.0
  points, and the run passed with only the missing card to say anything was wrong. A
  round nobody had played yet drew the rest on the dashboard beside the fixture, and
  nothing said so. Now no bye is credited, the dashboard draws the fixture and not the
  rest, and the finding names the fixture in the club's spelling and says which line to
  take out of `config.toml`. Where the fixture's card is missing it asks for the card
  there, and not a second time as `card-missing`. The two errors about byes the
  validator used to carry are gone. Both looked for a bye among the matches it is
  handed, and it is only ever handed the cards. Troubleshooting entry: *A team was
  awarded a bye it should not have had, or a round is short*.
- In multi-season mode `--discover` writes each competition's `season_key`, the name of
  the folder under `data/` its cards are in and the one thing a card there is filed on
  that is not printed on it. The file had the day and the division and not that, so a
  club keeping two seasons of one division was written a config its own next run
  refused — `[competitions.2025-tuesday-division-c] and
  [competitions.2026-tuesday-division-c] both claim day 'Tuesday' and division 'C'` —
  and so did `--discover --force`, which left the command that wrote the file unable to
  write it again. One season on its own was built only while its section kept the name
  it was given, and renamed, it was told nothing had been filed under it. A
  `season_key` a club types is read as its folder's name is, capitals and spaces aside:
  `"2026-Winter"` for `data/2026-Winter/`, the name as the folder shows it, claimed no
  card, and the cards were built beside the section under a generated id. The same
  refusal, and the F9 and `team-twice-in-round` advice about two years of cards in one
  competition, now fit the mode: a club that has set `seasons = "multi"` is no longer
  told to set it, and is told the half it has not done — a `season_key` for each
  section, or a folder of its own for each season.
- `--discover --force` writes back everything a competition declared except its draw. It
  dropped more than that. A captain spelt differently from the card came back as its
  slug, `"devi-o-raman"`; with the team list left open, a captain or court typed in for
  a team whose first card had not arrived did not come back; a bye typed `[4]` came back
  `["4"]`; a section no card was filed under that run was not written at all; the draw
  codes went; and so did the competition's own `scoring` and `min_sets_for_result`,
  which moved a ladder of 9, 6, 1 and 1 points to 19.0, 15.8, 11.1 and 10.7 under the
  preset, from the same cards and with exit 0. The team list was written from the names
  the page shows, so a team renamed under `[team_names]` came back declared as a team no
  card names, and the next run exited 1 and put it on the ladder under its new name on
  no points. And a season length only the draw gave went with the draw: `rounds_total`
  came back commented, and the front page's "Round 3 of 9" became "3 rounds played". The
  page spells a declared captain no card names as the club did, where it rebuilt the
  name from the slug and turned `Devi O'Raman` into `Devi O Raman`. The refusal that
  recommends `--force` names what it drops: the `[[…rounds]]` blocks of each section
  that has them, and any comment in the file, where it said only that everything
  declared "would be replaced by what the cards say".
- A declared draw is as long as the regular rounds it lists, whichever of them have
  cards yet. A round the draw listed with no fixtures stopped counting once its cards
  arrived, so nine rounds and a semi-final read "Round 3 of 6" at round three.
- A warning for a `[player_names]` line keyed on a name that `[namesakes]` splits.
  Once split, each player is known by the name and the team
  (`bo-nakamura-riverside-rockets`), so a correction under `bo-nakamura` matched
  neither of them, and the build that followed exited 0, showed both as the card
  prints them, and said nothing: a line the club typed did nothing, in the order a
  club meets the two sections, since the merged row is the one they corrected before
  the run told them it was two people. The line is given to neither player, not to
  both, because a split says they are two people and a name written for one of them
  is no evidence about the other. It is reported once per line, at every verbosity
  including `--quiet`, and judged against the players the cards built rather than
  against the file alone: the warning offers a line, keyed and quoted the way the
  section reads it, for each player the split actually made, and none for a team
  the entry misspells. It also names anyone still printed under the plain name in a
  team the entry does not list, such as a player of the same name in another grade.
  That line is still theirs, and the warning says to leave it where it is. The
  warning ends once any of the split players has a line of their own, and the rest
  keep the name their card prints, so a correction meant for one of them needs one
  line. A split that reached nobody is left to the `[namesakes]` warning that already
  reports it. `--discover` writes the line back as the club wrote it.
  `config.example.toml` now corrects one of its own M Kellys under the key the split
  gives her. README section: *Two different people share a name, and the dashboard
  has them as one player*.
- A greyed card on the front page gives the first round its section's draw dates: **2
  teams · round 1 on 6 Oct 2026 · declared in config.toml**. A section with its draw
  typed in and no card read yet gave `2 teams · declared in config.toml`, so the one
  thing a reader of the front page was waiting for was the one thing not on it. It is
  the first dated round in the order the season runs — a round 1 moved behind round 2
  is still round 1, not the earlier date — and never "next": the only today this
  program has is the date of the build, and a card choosing its round by that would
  decide from the calendar that a round nobody sent a card for has been played — which
  the dashboard refuses to decide that way everywhere else — and would have a rebuild on
  a later day rewrite the front page with no card changed. So the line reads the same
  the week after that night as the week before it, and a date gone by above *No results
  yet* is how a club notices the cards were never exported. The year is always given,
  since last season's draw left in read as a fortnight away without it. A round with no
  date is passed over rather than lent the next round's, a date of spaces counts as no
  date for every reader of the draw, a date typed in words is given as typed, and a card
  with results says how far its season has got instead.
- A greyed card that reads like another card under the same heading says which section
  it came from: **declared in config.toml as [competitions.tuesday-grade-b]**. The
  README's own worked example — cards printing `3 Grade B` beside a section that says
  `Division 3 Grade B` — listed *Tuesday Division 3 Grade B* twice under one heading,
  once as a link and once greyed, with nothing on the greyed one to say which section it
  was. Two sections with no cards did the same: a division written `C` and `Division C`,
  one night under two season keys in single-season mode, labels differing only in case,
  punctuation or spacing, and one night spelt two ways (`day = "Tuesday "`), which also
  gave the night two headings that read the same and now gives it one. The section
  is spelt the way the run's note about a section that matched nothing spells it, so the
  words on the card are words to search the report for, and it is named only when two
  cards under one heading read alike by the rule that decides whether two labels are one
  competition. No new warning comes with it: the run already says, once for each
  section, that nothing was filed under it. A card's small print now breaks a long name
  anywhere, since a section named with underscores made the front page 396px wide on a
  320px phone.
- The checks that read a card row by row — its scores against the set rule, its names
  against the rosters, one player on two courts, its ratings and its roster block — read
  exactly the matches that count as played, the only ones any game, set or appearance is
  counted from, because they ask the one rule that says so instead of keeping a list of
  statuses of their own; and a match's result, a forfeit's included, is taken only from a
  match that rule counts, for the same reason. Nothing a report says changes today: the
  lists agreed on all 216 combinations of status, scoreline, forfeit tick, Disputed box
  and opponent. What changes is that they cannot part. Parted by one status, a night
  washed out after two rubbers either failed the run over a fill-in "on two courts" — the
  card beside it and a night that did not happen — with six more warnings about rows no
  figure reads, or put its 12-3 into the ladder's games column with nothing in the report
  about the rows it came from; and a forfeit the rule stopped counting kept its win, its
  points and the findings on its rows while its 24 games left the games column. A washout
  is still named in the report as not counted, and its totals, its verb and its
  cross-checks are still read.
- The suite has one reader for the question ten of its tests ask of `app.js`: under what
  condition does this function print this sentence? There were ten copies of it, written
  one test at a time. Every copy passed the shipped script, but 35 broken versions of it
  got past one copy or another. A sentence whose whole block had been commented out, one
  printed from an `else`, one said a second time under another condition, a guard widened
  with `|| true`, and a guard whose term was left only in a comment inside its brackets
  all passed. The one reader, `guard_for()` in `tests/test_web_assets.py`, reads every
  printing of the sentence in the script with its comments blanked by the new
  `jslint.without_comments()`. It takes the `if` whose block actually holds the sentence
  and reads that condition to its own closing bracket, comments blanked there too. It
  refuses a condition that is not a conjunction, with a `||`, a `?` or a negated group
  anywhere in it, and a sentence that is not inside the call handing it to the page. It
  refuses a sentence in an `else`, and it accepts an early exit only where a test asks for
  one. Of the 397 earlier mutants of the script, these tests catch the same 138 as before,
  test for test.
- The paragraphs behind the rules banner's *What these mean* have a measure and spacing
  of their own, tested and explained in the stylesheet. Measured in a browser
  without the cap, the 161-character advisory runs as one 933px line on a
  desktop, and under `draws = false` the 143-character draws explanation runs as one of
  825px. With the cap, they are lines of at most 69 and 72 characters. The cap is in `ch`
  rather than pixels because a reader who sets a minimum font size of 16px would find a
  429px cap (the same width today) re-wrapping two of the lines and adding 48px to the
  banner. The tests ask for between 40ch and 60ch: at 7.8ch, a cap with its point out
  of place, the advisory is 22 lines of at most 10 characters. Without the
  paragraph's own margin, the browser's 1em above and below each line adds 50px to the
  opened banner at every width measured, from 360 to 1920. Both are read as the paragraphs
  get them, through the cascade on every screen the sheet names, so a rule such as
  `.rules-more p { max-width: none; }` is caught wherever in the sheet it is written, and
  a rule for a class that only starts the same is not mistaken for one that reaches them.
- The identity line may put a colon after its words — `Division: C / Yellow Ball / Round:
  1 / #1000100`, with or without a space either side — and may print the round as `Rd 1`
  or `Rnd 1`, the grade after its letter, `C Grade`, and any of them in capitals. Each of
  those was refused, as a page that "names both a division and a round" nowhere;
  `Division : C / Round : 1` was scored under a competition with no division and no round
  number, and `C Grade Round 1` as a division called `Round`. A separator is still
  required, so `Divisional Rules` is not division `al`; a value has to hold a letter or a
  digit and is never the next label, so `Division - C` and a template's blank `Division:
  Round: 1` are refused rather than read; and a grade after a division is half of its
  name however the two are separated, so `Division 3 Grade: B`, `Division 3 / Grade: B`
  and `Division 3, Grade B` are all `3 Grade B`, where each had been refused or merged
  with the `A` grade into one ladder `3`. The line is read from the left, so `C Grade
  Yellow Ball Round 1` is division `C` where it had been `Yellow`. Of the lines above the
  table, the identity line is the one nearest the table that prints either half, which is
  where the template prints it: below the letterhead, the title and the season line, and
  above the team names and the email address, which are not read for it. The first line
  printing both halves was read, so a title such as `Example District Division 1 Round
  Robin 2026` was read in place of the identity line below it, as division `1`, round
  `Robin` and no match id, and a title `Example District Division 1, Round 2 onwards` in
  place of an identity split over two lines below it, as division `1` and round `2`. A
  card that splits its identity over two lines is paired the same way, from the nearest
  line naming a division and the nearest naming a round, where a title `Example District
  Division 1 Pennant` above them had given division `1`. The short forms count only on a
  line that prints both halves, so neither a letterhead's `14 Example Rd` beside court
  `01` nor a title ending `B Grade` gives a card the half it does not print; a small `a`
  before `Grade` is the article; and `Round Robin` is not round `Robin`. `R` and a digit
  are not a round, which would make `Division R1` its own round, and `Pool` is not a
  division, being a word in clubs' names; no card measured prints either. A card refused
  for want of an identity line now says which wordings are read, from the same list the
  reader matches against, and quotes the line nearest the table that prints any of them,
  as the identity line is read, so neither a street nor a title is quoted in place of it.
  Only a page that prints none of those words is told so. `tools/doctor.py` looks for the
  division and the round with the reader's own rule before its own patterns, so it no
  longer reports either as not found on a card the reader places, or blames the header
  for a card refused over its table. A wording that was read before and is now read
  differently moves its cards to a different competition id: `3 Grade B` was division
  `B`, `Division Grade B` was `Grade`, `Division 3 Grade Round 1` was `3 Grade Round`,
  and `Division 3 Grade: B`, `Division 3 / Grade B` and `Division 3, Grade B` were `3`;
  they are now `3 Grade B`, `B`, `3` and `3 Grade B`. The run says so, with a note that
  `config.toml` describes a section nothing was filed under and the old dashboard listed
  under *left behind*. Changing that section's `division =` to the new name files the
  cards under it again, with its settings.
- `tools/fetch.py`: copies the scorecards an index lists into one folder, keeping
  only cards printed under the letterhead you name, never overwriting a card that
  is already there (one that now reads differently is kept beside it, as
  `<id>.rev2.pdf`, and reported), and failing when a round has fewer or more cards
  than its draw has fixtures. It reads from files on this computer and from a web
  server on it at a loopback address, such as `http://127.0.0.1`, only: it does not
  log in to Match Centre and cannot reach the internet. See *Collecting cards from
  an index* in `README.md`.
- A card whose checkbox labels name only some of the boxes it needs is refused, as one
  naming none is, and the refusal names each box it could not name by its place on the
  card. Printed with its boxes 1.5pt below their labels, a card names 24 of its 30, and
  the six it loses are every whole-match box: a forfeit ticked on one built at exit 0
  and was published as `played`, with the loss in three notes on the dashboard card and
  none in the report. It now fails the build as one F4 error, *this reader could not
  name 6 of the 30 checkboxes this card needs, so a tick in any of them would be
  invisible*, whose detail names each box (*both Match Winner boxes, both Match
  Forfeited boxes, the Match Cancelled box and the Match Disputed box*) and says why:
  the reach and line tolerance a square has to meet, the nearest words the page prints
  for a label it never read, or how many times it prints a label that is short of its
  boxes. Of seven one-card builds with the boxes moved 1.5pt or a set of labels
  reworded, all seven were read, three of them at exit 0: four with a match status other
  than the one ticked, one a forfeited set short and two with a retirement unseen; all
  seven are now refused. Every box is asked for by its place, a per-set box by its
  label, set row and side, so a card with row 1's home Forfeit square gone and a box
  drawn again off the table, which names 30 of 30 and was read without a word, is
  refused as well, as *row 1's home Forfeit box*. The nearest label and square are
  paired first, within the label's reach. Each label in turn had taken the nearest
  square still free, so the label whose square was gone took its neighbour's, and a
  retirement ticked there read as a forfeit. A card naming more boxes than it needs is
  read as before, with its warning. A card on which every square found is named, or
  which prints a label fewer times than it has boxes for, is refused as one that lacks
  part of its page, and the doctor says to download it again instead of blaming another
  template. Two files of the hostile corpus are refused that way where they were read:
  half a card, 9 short of the 24 boxes its three rows need, and a card with one byte
  flipped mid-stream, which loses row 2's away Retired label. So is every cut of a
  genuine card from 30% to 79%, where 43 of the 50 were read. Troubleshooting entry: *A
  card was refused*.
- A fill-ins heading printed word for word the same over both squads, `Emergencies` or
  `Spares` alone, ends them instead of joining them when nothing else on the card
  settles it. Such a line names no side and holds nothing a name cannot, so it read as
  two players who share a name, one in each squad, and a fill-in printed under the
  home heading was registered to the home team. Measured on both legs of one fixture
  with one fill-in, at exit 0: 8 players where there are 7, the heading on both teams'
  squads, and 4 warnings where the template's own headings give 3, one advising a
  `[namesakes]` entry for the heading and one calling the return leg's squad short of
  the fill-in, whose `unknown-player` had gone. With no `Comments` heading as well,
  neither squad was read. Such a line now ends both squads when the block ends at
  `Comments` or the foot of the page rather than at a word beginning `Fill-in`, no
  other heading ends either squad, and it is one line printed across both, under
  somebody, with nobody rated from it down in either squad and no set row naming it;
  of several, the first, so a placeholder or a name printed under both copies stays
  under it. `Emergencies`, `Spares` and `Fill Ins` each now give the same 7 players
  and 3 warnings as the template, `Emergencies` with no `Comments` heading too, with a
  comment beginning `Fill-in`, and with `None` printed under both copies; each card
  notes the line it took, quoted. `Reserves`, `Substitutes` and `Reserve Players` give
  the same with no note, since the reader now knows them by the word (below). The cost
  is one shape of two real people: two namesakes printed unrated and level at the foot
  of their squads who both sat the night out, over no heading this reader knows, now
  end the squads there, with anyone printed unrated under them, and the card says so.
  Measured on two legs, that season had 7 players and a `player-two-rosters`, and now
  has 6 and a note on each card; printed above a heading over both squads, it had 9
  players and 4 warnings, and over `Emergencies` now has 7 and 3, as if they were not
  printed. Over `Reserves`, which the reader now knows by the word, it has 8 and 4,
  both of them kept (below). A rating for either of them, or a heading the rules
  before this one know printed under them, reads the card in full. A comment beginning
  `Fill-in` on a card with no `Comments` heading still leaves `Emergencies` on both
  squads, as before; a `-` printed under both copies no longer does (below).
  Troubleshooting entry: *The report says a card's roster was not read*.
- A night two cards disagree about says so on the page, on every card of it. Each match in
  `season.json` names the other cards for its fixture in `other_cards`, by their places in
  its `matches` and in the order the build read them, which is the order the error and the
  data health section list them in. By place and not by `source`, because a page built
  without names withholds every file name that holds a player's name, and two such files
  read the same. It is `[]` for a card alone, and written for a copy as well as for a
  clash. The card's summary now starts `cards disagree — counts for nothing`, and every
  row of it in the season's table of rubbers says the same in the Night column, where it
  said `Played` beside a winner. A washout card of such a night says both, `cancelled,
  cards disagree — counts for nothing`, so the page still says which card was the washout.
  The opened card carries a `Not counted` warning that names the other files and says the
  night is counted once every card that is wrong has left the data folder, which is as true
  of three readings as of two. When the two cards printed the same match id, `#/matches`
  showed only the one read last, silent about the clash. It now shows every card of the
  night together: measured on the demo with a second round 2 card that disagrees, 16 cards
  and 64 rubber rows where it showed 15 and 60. With different ids both were shown already,
  and now both say so. When the card read first printed no match id, the fixture found no
  card and the page showed the other one alone, 15 cards and 60 rows; now it shows both, 16
  and 64, at their fixture. A washout card of a clash no longer says what its round is worth:
  under `unplayed = "average"` it said the round was credited the team's own average, and
  the team's row was the same as under `zero`. The field is new in format `2`, and a page
  given a document without it names no other file.
- Finals run in the order they are played: QF, SF, PF, then GF or F. A round that is not
  a number had been put in order by its letters, so `QF, SF, GF` ran `GF, QF, SF` and `SF,
  F` ran `F, SF` everywhere rounds are listed: the season document, the Matches page, the
  report, the draw a first run writes, *since the last build*, and the round given on a
  front-page card whose draw is only its finals (`round F` where the series starts with
  `SF`). The overview's latest round was the semi-final once the grand final had been
  played, and a player's rating was the one printed on the semi-final's card. With the
  draw declared, a semi-final card with no grand final yet reported the grand final's card
  missing, and a grand-final card left a semi-final with no card unreported. A name that
  is not a final, such as `Catch-up`, is scored as a regular round and goes after the
  numbered rounds and before the finals. The order comes from each round's label and never
  its date, which a declared round need not have. Two labels in one place, `3` and `03`
  or `SF` and `sf`, are two rounds and run in text order within it, `SF` before `sf`,
  except that a number printed plainly comes last in its week, so `03` runs before `3`
  and the ladder after round 3 has the `03` card in it. They had tied, so their order in
  the season document, the report and the ladder's steps changed with the hash seed, a
  rebuild with nothing new could rewrite `season.json`, and the ladder after `03` was
  the ladder after `3`. The report's spare cards, teams twice in a round and players for
  two teams, and the document's list of duplicates, ran in text order, round 10 before
  round 2 and GF before SF, and now run in the season's. A site with finals in it, with
  such a pair, or with one of those lists naming rounds that text puts in another order,
  rebuilt, rewrites its `season.json` and `season-data.js` in the new order. The
  dashboard now reads its order from the season document rather than keeping its own
  copy of the rule, so every site rebuilt rewrites its `app.js`.
- A finals label is read as one when it is hyphenated, run together, plural or numbered:
  `Semi-Final`, `SemiFinal`, `Semi Finals`, `Grand-Final`, `Quarter-Final`, `SF1` and
  `SF 2`. `Qualifying Final` and `EF` are read as finals too. These had been scored as
  regular rounds, in the ladder and with F11's warning that the label was not one it knew,
  and a declared draw with no `rounds_total` counted them in the season's length. They are
  finals now, out of the ladder and the length, and each runs in its week of the series. A
  season whose cards print one of them gets a different ladder on its next build.
  `config.example.toml` lists the labels read as finals. It had said any label that is not
  a number is treated as finals, and the report's finals note and an F11 error said the
  same. None of the three was true, since a `Playoff` card is scored in the ladder with a
  warning. The two messages now say a label that names a final.
- A draw that gives the season its length, over cards that have played past it, is
  reported once, and as the draw. A draw dating round 1 and listing nothing else, over
  cards for rounds 1 to 3, stopped the run with F3's `rounds_total is declared as 1 but 3
  regular rounds have been played (1, 2, 3)`, about a `rounds_total` nobody typed, and the
  same run said so twice more: in a note that cards were found for `rounds 2, 3, which the
  declared draw does not list`, and in one that the season `is declared as 1 rounds long`.
  F3 now says `the declared draw lists 1 regular round, which with no rounds_total is the
  season's length`, names the rounds the draw leaves out, and says to add them to the draw
  or set `rounds_total`; neither note is printed beside it. A `rounds_total` you typed
  keeps its own sentence, and the note restating the length is left out beside that one
  too. `--discover --force` wrote the draw's length back live, as `rounds_total = 1` with
  the draw gone, and every run after it exited 1; it is written commented out now, under a
  line saying why, and the next run exits 0. The rounds that file says the dashboard shows
  without a length are the rounds with cards, counted as the page counts them: counted off
  the draw, a draw of rounds 1, 2 and a semi-final nobody had played, over cards for three
  rounds, wrote `"4 rounds played"` and `# rounds_total = 4`, and a week the byes table
  rests every team in was counted as a round played. A season declared one round long and
  played to it is `1 round long`.
- A player who decided no set has no win rate, where they had one of 0%. A name on a
  roster who never took a court and a player whose only rubber was retired were
  published with the same `win_pct` of `0.0` as a player who lost all six of theirs, and
  the page printed all three as 0.0% on the player page, the team page and the Players
  tab. Copy and CSV exported them as `0.0%`, and sorting by Win % put them among the
  real noughts by name. Only the third is a record. `season.json` now gives the other two
  `null`, as it gives a team with nothing counted a `null` points average. The player
  page's Win rate tile and the Win % column on the team page and the Players tab show a
  dash, Copy and CSV leave the cell empty, and the sort puts them below every real 0%.
  The slot table lower down the player page now rates each slot by the same rule, as
  the entry on set slots below says. The Win % heading, whose words are printed under
  both tables, now reads **Sets won as a percentage of completed sets**, which is what
  the player page's tile has always said. It read *of sets played*, which a retired set
  had already made untrue for 4 of the 18 players in the demo season. The first build
  after upgrading rewrites each competition's `app.js`, its `season.json` and
  `season-data.js` where a player has decided no set, and the one-file page where one is
  built, and the next build writes nothing.
- Whether a match was played is written into `season.json`, as `counts_as_played` on
  every match, and the page reads it there instead of keeping its own list of the
  statuses that count for nothing. The list was a second place a new status had to go:
  added to the Python and missed in the browser, it would have left the front page's
  tiles counting a night the ladder beside them does not. The season document's format
  is now `2`. It adds `counts_as_played`, and `other_cards`, the places in `matches` of
  the other cards for each match's fixture, and it gives `win_pct` as `null` for a
  player who decided no set, where format `1` gave `0.0`. A script of your own that reads
  format `1` has those three to allow for before it accepts the number, and in a document
  built without players' names, `meta.players`, `meta.players_detail` and `withheld` in
  `meta.provenance.players` besides. The only build that leaves this page beside a
  `season.json` from before the field is one that failed partway through writing the
  site, and says so; opened like that, the page shows its *Schema version* note asking
  for `python3 build.py` to be run again, and shows none of the older document's season.
- A card the build refuses is reported as one mistake, however many divisions the run
  builds. Every competition's document still names every refused card, because a card
  refused before its division was read could belong to any of them. The report, though,
  printed each refusal under F4 once per division and then counted each copy again as a
  validation error, after `card(s) found and not scored` had already counted it.
  Measured on the demo with two PDFs refused, one cut to its first 1,024 bytes and one
  made unreadable, one division ended `exit 1: 2 card(s) found and not scored, 2
  validation error(s)`, three divisions ended with `6 validation error(s)`, and four
  ended with `8 validation error(s)` under `== 8 errors ==`, in a 73-line errors
  section. All three runs now end `exit 1: 2 card(s) found and not scored` under `== 2
  errors ==`, and the section is 16 lines. `N validation error(s)` now counts the errors
  about a competition's own cards and leaves out the refusals the clause before it has
  counted.
  A run with both kinds says `2 card(s) found and not scored, 1 validation error(s)`.
  Each refusal is printed once with no competition's name in front of it, and the errors
  heading counts what the section prints. That is once per refusal, not once per file: a
  card with two blank pages printed after it has both pages refused, and each is printed
  once, as `page 2` and `page 3`. Two divisions whose warnings read word for word the
  same still get one entry each. The exit status is 1 as before.
- F4 gives a refused card's detail once. Its message was built from the refusal's
  one-line description, which already carried the detail in brackets, and the detail was
  then given again as the entry's own field. The report and the page's data-health list
  both printed it twice. The unreadable card's message was `data/locked.pdf: not a
  readable PDF (data/locked.pdf: cannot read ([Errno 13] Permission denied:
  'data/locked.pdf')) — found under data/ and not scored.` and is now `data/locked.pdf:
  not a readable PDF — found under data/ and not scored.`, with the detail beside it. In
  the report the entry names the path 4 times instead of 6, and on the page its item
  gives the detail once. Together with the change above, the cut card's 234-character
  detail now appears twice in the report, once under `refused:` and once under F4.
  Before, it appeared 3 times for one division and 9 times for four. A card on its second
  page still says `page 2`. A refusal with no detail is now given none; before, its
  reason was repeated as the detail.
- `tools/doctor.py` no longer calls a page that prints a card's words a picture of one.
  Any page with words and no rectangles was told *the page paints no rectangles, so it
  is an image of a card rather than a card*, that a scan or a screenshot printed to PDF
  cannot be read, and to re-export the scorecard from Match Centre. That included the
  fixture card with every rect filled as a closed path instead, the way LibreOffice Draw
  saves a card it has opened, which prints all 197 of its words and 16 of the 17 header
  fields, and it included a draw typed out with its rounds. Such a page is now called a
  picture only when no header field is found on it but the date and the time, which
  Chrome prints at the top of the page when it prints with its headers and footers on.
  The card's screenshot printed to PDF from Chrome finds those two and nothing else, and
  it is still told what it always was. So is a page that finds no field, or only those
  two, such as a team list, a rules sheet or a fixture list typed out, which is no
  picture either (filed). Any other page with no rectangles is told that its shapes are
  drawn in a way this reader does not record. The sentence names the two things that
  draw a page so, a program that opens a card and saves it again and a scanning app that
  lays a card's words over a picture of it, since this reader measures the two alike. It
  asks for the card to be exported again from Match Centre, and for the verdict to be
  sent on if that export is refused too, and it says that a draw, a rules sheet or a team
  list typed out can look like this too. A screenshot whose title names a round, a
  division or a match id finds those fields as well, and is now told this rather than
  that it is a picture. The advice, to export the card again, is the same (filed).
- The doctor's two causes that end with a word for a page that is no scorecard, the one
  above and the one for a header line missing its division, its round or its date, now
  say the same thing: the file is fine, but keep it in a folder of its own outside
  `data/`, because `build.py` reports every PDF under `data/` that it cannot read as an
  error (F4) and exits 1. The missing-header cause said that nothing was wrong, since
  `build.py` counts such a page as refused and carries on. It does count it as refused
  and it does write the site, but it reports the page as an error too: a build with a
  draw and a card with no header line beside four demo cards reported both under F4 and
  exited 1, as the README says it will.
- The review list asks how many sets a result needs, once, while nothing in `config.toml`
  says so and a match was played that decided nothing. `min_sets_for_result` is the one
  setting no card states that can take a played match out of the ladder, and a club that
  never set it was told only that each such match had no result: two matches with 2 of the
  3 sets finished gave two `no-result` warnings and, where Match Centre printed a winner,
  two `verb-no-result` errors and exit 1, with the setting named nowhere. The item comes
  last, offers the number in force (`[rules]` `min_sets_for_result = 3`), so adopting it
  changes no result, counts the matches and how many of their cards print a result
  anyway, and says to check `[rules] sets` first when a stopped set has no box ticked,
  since a set played to 4 read as one to 6 looks the same. Where the competitions in one
  run need different numbers it names each competition's own section and number instead.
  A washout, an unplayed card, a bye and a forfeit are never asked about, nor is a number
  the club declared at either level. A `[rules]` number too large for a competition's
  cards now names the file, the line and the competition, and says to lower it or move
  it, where it had said only `min_sets_for_result=3 is outside 1..2`. A card that lost a
  row is reported as the cards disagreeing, naming it, with a number declared as without
  one; with one it had been reported as that number being out of range.
- `config.toml` offers the shape and the sets a result needs that the cards already have.
  It had written `# format = "doubles-singles-doubles"` and `# min_sets_for_result = 3`
  under `[rules]` whatever the cards were, while the review list said of a two-doubles
  club's `format = "doubles-2"` that it was "commented out; delete the # to adopt it":
  deleting the `#` refused every card, and deleting the one before the number stopped the
  run. A single shape or number is offered under `[rules]` only when every competition it
  would reach has it; otherwise each competition's is written commented in its own
  section, and the review list's format item names that section.
- A disputed card that falls short of a result says it has none. It had said "The result
  is counted as printed until the club says otherwise." and then, for the same match,
  "recorded as disputed, so it is not counted in the ladder.", and both were false: it now
  says the box is ticked "but the match has no result for the ladder to count", and the
  `no-result` line says how many sets it was short. A disputed card with a result is still
  counted as printed.
- `min_sets_for_result = 0`, or less, is refused, under `[rules]` or a competition's own
  section, naming the file and the line. A 0 had been read as leaving the line out, so
  the run used `[rules]`' number or the default instead, and a -1 drew only
  `min_sets_for_result=-1 is outside 1..4`.
- `[competitions.<id>.expected]`: type in your association's published points and
  matches played, and each build checks the dashboard against them. A team that
  disagrees is named with both figures and what each of its rounds gave, and a
  difference the way a week off is scored explains is one warning naming the setting.
  No figure on the dashboard changes; a disagreement joins the data-health list as a
  warning (`sheet-mismatch`, `sheet-ahead`, `sheet-team`) and the build still
  succeeds. A line whose key names no team asks after the team nearest it, and two
  teams equally near are both named and neither is chosen, in the words the
  `[player_names]` check uses for a tie.
- `tools/scheduled.py`, the command to give cron or Task Scheduler. It builds only when
  a file under `data/`, `config.toml`, the program or `site/` has changed since the
  last build. It keeps each build's full report in `reports/`, newest last, the newest
  30 by default (`--keep`), and never writes a report identical to the one before. It
  exits with `build.py`'s status (at least 1 when a build put players' names back,
  below), and a run that found nothing new exits with the
  status of the build it skipped. A build still running after `--timeout` minutes
  (default 10) is stopped. `--print-schedule` prints the crontab line or the Task
  Scheduler fields, and refuses a `--reports` folder a run could not create or write
  in. It gives `build.py` only `--data`, `--out` and `--config`, so a copy scheduled
  without names needs `[build] players` in the settings file it is given, and its line
  ends by saying where when a build printed players' names in a dashboard that
  withheld them before it.
- The front page now says so when the last build failed, in one sentence above the list
  of competitions, and the next build that works takes it away. A failed build changes
  only `site/build-status.js` and leaves every dashboard as the last good build wrote
  it. A build that worked writes the same file every time, so an unchanged rebuild
  still writes nothing. A one-competition site is now 9 files rather than 8. A `data/`
  folder this account may not list is refused with its error and exit 2, where it was
  reported as holding no scorecards. A build that stops rather than write a player's
  name into a dashboard that withholds them says so on the front page too, in its own
  sentence.
- A card that counts for nothing registers nobody and rates nobody. The squads were
  gathered from every card's roster block while the checks that read a roster read only
  the matches that count, so a name printed on a washout's roster and nowhere else
  became a registered player with no matches, and every other card of the team was told
  its roster had left them out: added to the Magpies' round-five washout in the demo,
  one name took the report from 6 warnings to 10, four `roster-incomplete` on the cards
  that were right and none on the one that was not. The squads now come from the cards
  that count, which a night stopped short with no result still is, and the rating
  history from the same cards, so a washout's ratings are no longer a round of
  anybody's: round five had been in six players' histories on the demo, and left them if
  the washout printed no ratings, with nothing in the report either way. A `(C)` on such
  a card names no captain either, and the note for a team left with none says that no
  card that counts prints one. A captain declared in `config.toml` and printed only on
  such a card is no longer a registered player with no matches: the note saying no
  player carries the mark says why. Nor does the review list count a declared captain no
  squad holds among captains "inferred" from the cards, which it had done beside that
  note for one printed on no card at all, offering the slug where the club had typed the
  name. A folder none of whose cards has been played yet registers no squad and no
  captain until one has: the demo's round one alone, every card unplayed, had 18 players
  and 6 captains from its `(C)` marks and now has none, exit 0 and 8 warnings either
  way. One note says why, in place of the one naming the six captains, and the
  `config.toml` a run writes for it lists the six teams' captains commented and blank,
  where it had them live, under a comment that a `(C)` names a captain only on a card
  that counts.
- The byte-identical rebuild test builds a match with two fill-ins, so the order they are
  written in is held to the byte too. The demo season it builds has one fill-in, in one
  match of fifteen, and a list of one name has one order: with a match's fill-ins written
  in whatever order a set gave them, every build it compared came out the same, and so did
  the whole suite. Each demo card with a fill-in on it now reaches that test with a second,
  a player on no roster in the place of the last player on the home roster, and the same
  mistake fails three of its four stages. So does the list of players a card gave no
  rating for written in set order, which the second fill-in reaches as well. The demo is
  unchanged: it is the season a club builds first and the one the README quotes, and a
  second fill-in on one of its cards, a stranger or a player lent by another team, is a
  seventh warning in that report.
- A fixture gone from a dashboard whose `season.json` names no file for it is said in a
  sentence, `R2 Hilltop Aces v Parkview Wanderers, and the last build recorded no file for
  it`, where the line read `last read from no file recorded`. A build records the file
  every card was read from, so only a document edited by hand or written by something
  else gets there, and no test did: with those words taken out the line stopped at `last
  read from`, and the whole suite passed.
- **`[build] players` and `--players`: build the dashboards without players' names.**
  `numbered` shows each player as their team and a number (`Brindle Magpies #3`),
  `initials` as their initials (`P.H.`), numbered where two share them. Every season
  document and the front page are searched for players' full names, and for each line of
  a squad that a card's note says was read as a heading, as the card printed it, before
  anything is written, and the build writes no dashboard if one survived. Such a note is
  withheld whole, unless the line is a player's name, which it names by their label, or
  one the page prints whole anyway, such as a team's name. The one file a refused build
  may change is the front page's `build-status.js`, to say the last build failed, and
  never under `--check`. An `error:` line on stderr says so, naming nobody. A `players`
  line that is misspelt, misplaced or both stops the build, unless it asks for names or
  `[build]` already withholds them. Every command the report tells a `--players` run to
  paste carries the same `--players`. See
  [Publishing without names](README.md#publishing-without-names) in the README.
- A warning for a `[player_names]` line that no player is looked up under. A key no
  card prints (`no-such-person`), a key a letter out (`bo-nakamuru` for `bo-nakamura`)
  and a key on the left-hand spelling of an `[aliases]` line each exited 0 with not a
  line of the report about them, and the first two left the report line for line as a
  build with no `[player_names]` at all writes it. Each is now reported once, at every
  verbosity including `--quiet`, with the key to use where one is near: the alias's
  target, since a name is looked up after its alias, or the one key nearest the typo,
  a player's or a name only a card that counts for nothing prints, with the line to
  paste. Two keys equally near are both named and neither is offered, and a key whose
  target already has a line is told it can go. No line is offered where the cards
  cannot say who it is for: an alias into a name no card prints, or a key spelt from
  the very name it gives, like `jo-calden = "Jo Calden"`, which may be for a player on
  none of the cards and, pasted as offered, renamed `Jo Calder`. A key meant for a name
  `[namesakes]` splits, a letter out from it or folded into it by an alias, is given a
  line for each player the split made, in the words the warning about a line under
  that name uses. A key `[namesakes]` builds from a name and a team is left to the
  namesakes warning, and a line under a name the split made players of, to the warning
  that already covers it. A line under a `[namesakes]` name that split nobody is
  judged as any other line is: before, only the note that the entry separated nobody
  spoke, and `--quiet` leaves that out. A folder with no cards in it says nothing about
  any line. A line under a name printed only on a card that counts for nothing, a
  washout or a night not yet played, is a note that it waits for that player's first
  card that counts, and is offered no key. Before, the run warned that no card printed
  the name and, where a counted player's key was near, offered a line under that key,
  which pasted renamed that player.
- A `[player_names]` line now reaches a fill-in named only in a set row. Those names
  were spelt from the slug and the table was never read for them, so a fill-in printed
  `Tui-Mae O'Dowell`, on no roster anywhere, showed as `Tui Mae O Dowell` with a
  correction for her in the file. A player registered in one grade and filling in for
  another takes the line in both, where before it reached only the grade that registers
  them. A set-row misspelling of a registered player is corrected the same way, and is
  still reported by `unknown-player` as before; a line that gives it the registered
  player's own name now shows two records under that one name.
- The lines behind the rules banner's *What these mean* keep to 80 characters, the
  width WCAG 2.2 asks for at level AAA (1.4.8), in each of the four faces they were
  measured in: Noto Sans, Liberation Sans, Nimbus Sans and DejaVu Sans. The faces
  system-ui is on other systems were not measured. Their cap had been 78ch, and a `ch`
  is the width of a zero, which is wider than this text's average letter. Measured in a
  browser, the demo season's advisory ran to a line of 92 characters and its unplayed
  explanation to one of 84, and 24 of the 100 different paragraphs it can show under
  the named settings had a line over 80, up to 104 for a six-singles format. The cap is
  now 60ch, the widest whole number at which all 100 keep to 80, as do the 1022 formats
  of one to nine slots under each of the eight set rules, at most 79 characters a line,
  in each of the four faces and in light and in dark. At 61ch, four singles under a
  tiebreak rule are a line of 82 in DejaVu Sans. It costs the demo's opened banner 38px
  at 1280, 230px where it was 192, and 56px under `draws = false`, 291 where it was
  235, and nothing on a phone, where the banner is already narrower than the cap. 65ch
  would have left 18 of the 100 over 80. Rewording would have taken three of the demo's
  sentences, two of them shared with the run report, and left every format as it was,
  since a format's explanation is built from its slot names. With the cap in place, a
  wider `min-width`, a `display` that runs the paragraphs on or sets them side by side,
  a `white-space` or `text-wrap` that refuses a break, and a negative `letter-spacing`
  or `word-spacing` each brought back a line over 80, from 82 to 210 characters. The
  tests refuse each of them on every screen, given to the paragraphs or to the
  disclosure or banner they inherit from.
- **Careers across seasons.** In multi-season mode the site gains `careers.html`, linked
  from the front page and from every player's page. It shows each player's seasons, one
  row per season and grade, added up. Every total is the sum of the rows above it, and
  every row opens the season page it was copied from. Win rate is worked out again from
  the added-up sets, and is a dash where no set was decided. Ratings, streaks, partners
  and team share are shown per season and never added up. People are matched by name
  across seasons and grades, and `[namesakes]` tells two people apart. The page is
  plain HTML with no data in a script, so it opens by double-click with JavaScript off,
  and a one-file dashboard has no link to it. The report's `cards` section says how
  many careers span seasons, `careers      7 of 9 player(s) played in more than one
  season`. A season document without one of the counts the page adds stops the build,
  and stops `--check` in the same words. A `careers.html` this tool did not write, or a
  link in its place, whatever it points at, stops the build too: it is named and left
  as it is. Switching back to single-season mode lists the page under *left behind*, and
  `--prune` removes it. A build that withholds players' names writes no careers page,
  in multi-season mode too, because a withheld label belongs to one season and not to a
  person; the report's `careers` line says so, and a page an earlier build left with
  names on it is listed under *left behind* until `--prune` removes it. Single-season
  mode is otherwise unchanged. A `[namesakes]` line
  naming teams from different grades is no longer called useless in multi-season mode,
  because it tells two careers apart.
- `--out` pointed at a folder named `index` no longer offers the front page as the
  one-file dashboard. A folded page was known by its name alone, and a folder named
  `index` gives a fold the front page's name: a build without `--single-file` printed
  `one file .../index/index.html (14KB)`, a front page with no dashboard in it, and a
  build with it printed `2 written, one beside each dashboard (380KB in all)` for one
  dashboard. A fold now also has its season's data file beside it, so the two builds
  print nothing and the one real fold.
- `build.py --only <season>`: in multi-season mode, lists one season in the report and
  counts the others in a *not listed* section after the warnings. Every season is still
  read, checked and written, so `site/` is byte for byte what a run without it writes.
  Refused cards, validation errors, the changes since the last build and dashboards left
  behind are listed from every season, and the exit status is the whole run's. With
  `--single-file`, adds the one-file page to that season only. It cannot be combined
  with `--prune`, which works on every season. A name that is not a season is refused
  before anything is written, with the list of seasons found, and a season's label, a
  competition's id or a path to a season's folder is told the name to give instead. A
  season `config.toml` names is accepted before its first card. `--o` no longer
  abbreviates `--out`, because `--only` starts the same way; write `--out` in full.
  The review's `[rules]` question about the sets a result needs can cover several
  seasons. It is listed if any of them is, naming every competition it is about, the
  listed ones first, and saying how many of its matches are in seasons `no-result`
  does not print. It is counted under *not listed* only if none is. The cards
  section's `seasons` line names every season, with `--only` or without, and now
  wraps under its value: at ten seasons it ran to 150 columns. So do a competition's
  `cards` line and, with no season length declared, its `rounds` line: at eighteen
  rounds they ran to 95 and 79. Three older messages list as `a, b and c` where they
  joined with commas alone: `player-two-teams` naming a player's teams, F8 naming an
  unknown team's matches, and the note naming the fixtures a declared round lists
  more than once.
- A whole-match forfeit written the usual way, with the Match Forfeited box ticked
  and every set row left 0-0, builds clean. It was reported as a card contradicting
  itself: two F6 errors, one from the result verb and one from the Match Winner
  tick, each held to rows that nobody played, and a `set-no-games` warning for every
  rubber, so the build exited 1. A card that also left the absent side's names off
  those rows was told `set-players-missing` as well. The verb and the tick are held
  to the box instead, and agree with it; `tools/doctor.py` says so too. On rows left
  blank, a verb or a tick for the side the box says forfeited is still reported,
  once, where it was reported up to three times: a verb by `verb-mismatch`, and a
  tick by F6 where no verb names that side too. Rows giving the match to that side
  under a verb and a tick for the other are reported once, where they were reported
  twice. Where one cross-check leaves its mistake to another, the doctor says it
  `disagrees, reported by` that check, and counts it apart. Under the `rubbers`
  preset the side that turned up is paid every rubber of the match's format and the
  other side nothing, where both were paid 0.0; that is what the same night pays
  when every rubber is ticked `Forfeit` against the absent side, so the ways of
  writing one night give one result under every preset. Nor do a forfeit's rows,
  where some were filled in, change what `rubbers` pays: rows that gave the absent
  side every set paid it 4.0 and the side that turned up nothing. Every card that is
  not a whole-match forfeit, and every other preset's points, are unchanged.
- The `config.toml` a first run writes has everything the run worked out behind a `#`:
  the club's name as the letterhead prints it, the title made from it, each team's
  name, each captain read from a `(C)` mark and, in multi-season mode, each season's
  name. Written live, the next run read them back as the club's own: over the demo
  season, a second run with no new card rewrote 4 of the 9 files, where `season.json`
  moved the teams from `derived` to `mixed` and the captains from `card` to
  `declared`, and it listed one review item fewer. In multi-season mode, 7 of 15, and
  a second `--discover --force` wrote each season's name live in its section as well.
  A second run now writes nothing and says so, and its dashboard is byte for byte the
  first's. A line counts once you remove its `#`, and the report's notes on the
  letterhead's name, the team names and the captains print on every run until you do.
  Each now names its table and says so, and the captains' note no longer says a
  captain read from a `(C)` mark is shown as derived, which no page did. The notes on
  the team names and the captains list their teams as `a, b and c`, where they were
  joined by commas alone. A
  `config.toml` you already have is read exactly as before. The suggested title is
  also made from the club's name where you gave one: made from the letterhead, a
  rewrite over a corrected name headed the site in the letterhead's capitals again.
- A card whose letterhead differs from the rest of its competition's only in capitals,
  with or without other spacing, is no longer reported as possibly another club's. The
  `foreign-card` check folded a letterhead's spacing and kept its case, where the demo's
  own-card test and `tools/fetch.py`'s keep test fold both. So the demo with one card
  printing its club's name in title case built with a seventh warning, about a card the
  fetcher had kept and the demo called its own. The check now asks the same
  `letterhead_key`. The letterhead it names is quoted in every spelling its cards
  print, each counted, and the rest of the competition the way most of its cards print
  it, as "3 of the other 9 card(s)" where some print it another way. A tie of spellings
  goes to the one read first, and so does a tie between two clubs' letterheads.
- The `next` line that `tools/demo.py` and `tools/fetch.py` print works pasted where
  you ran them. The demo named its folder as you typed it, after a `cd` into the
  project, so `--out season` from anywhere else printed `--data season`, and the build
  answered `no such directory: season`. Nothing in the line was quoted, so a folder
  named `our season` pasted to `unrecognized arguments: season`, and a project kept
  under a folder with a space in its name to `can't cd`. Of 11 demo runs tried, 6
  printed a line that failed when pasted, and so did 2 of 5 fetches. The folder is now
  resolved against where you stood. One inside the project is named from the project,
  as `data/` is, and one outside it in full. Both, and the `cd`, are quoted the way the
  build quotes its own hints, by the same function. All 16 lines now run, and so do
  a folder with an apostrophe in its name and one in the project whose name begins
  with a dash, which is given a `./`.
- A card or a declared draw round labelled with a very large round number no longer
  makes the sheet check list every whole number below it. With the sheet typed after
  it, a card labelled round 1000000 built a 455,917-line report in 4.9 s and 540 MB,
  its advice reading `Nothing was read for it in rounds 3, 6, 7, 8, ...` all the way
  to a million. Three or more numbers in a row that no card and no draw has are now
  one entry, `R6-R999999 nothing read`, and are said as `rounds 3 and 6 to 999999`.
  One or two are still a round each. The same report is 233 lines, in 0.7 s and 32
  MB, whether the card says round 1000 or round 1000000000000.
- A `config.toml` holding a whole number too long for Python to print, more than
  4300 digits, is refused with exit 2 and one line naming the file, where it used to
  stop with a traceback: `error: config.toml: not TOML this can read — a whole number
  in it is more than 4300 decimal digits long`. The same number in hexadecimal is
  refused the same way; it used to be read, and then crashed the build wherever it
  stood for a round or a season. So is a file nested too deep to read, which printed
  a 3,002-line traceback. None of these breaks TOML's own rules, so none is called
  invalid: `not valid TOML` is still said, with its line and column, only of a file
  that does. `tools/fetch.py` refuses such an index with the same words, before it
  fetches anything, because both files are now read by one function. Not every such
  number is caught yet: a round labelled with one, in quotes in either file or
  printed on a card, still stops with a traceback.
- A team that a sheet line nearly names, such as `"Brindle Quols"` for Brindle Quolls,
  is now listed among the teams not compared as `nearly named -- see sheet-team below`.
  It used to be listed as `not typed in`, right beside the warning asking whether that
  line meant it.
- The sheet warning names a round whose cards were all set aside, and that round's
  entry counts them. Two cards that disagree about a match are both set aside. The
  entry used to read `R4 card set aside` once for both, and the advice was `It had no
  week off, so the difference is in one of its matches`, which sent you to a match
  that was never counted. The entry now reads `R4 2 cards set aside`, and the advice
  says `Nothing from round 4 was counted for it, and the finding about that round says
  why: deal with it first`.
- The sheet warning for a team with nothing read in several rounds now says `If it
  played those weeks, those cards have not been exported`. It used to say `that week,
  that card` however many rounds it named. Each word in it that agrees with the
  rounds is now chosen by the same count that picks `round` or `rounds`, and a run of
  rounds counts as the weeks in it.
- A card refused over its checkboxes names each box it lost when a square and the next
  label have gone together. With row 1's home Forfeit square and row 1's home Retired
  label missing, it was refused for *row 1's home Retired box* alone, after saying every
  square it found was named, because the Forfeit label had taken the Retired square
  beside it. One label along, with the Retired square and the Incomplete label missing,
  the Retired label took the Incomplete square in the same way. A Forfeit, Retired or
  Incomplete label now names only a square on its left, where the template prints it.
  A whole-match label still names the nearest square on either side, since the away
  column's Match Winner and Match Forfeited boxes are on the right. The two cards are
  refused for *row 1's home Forfeit and Retired boxes* and *row 1's home Retired and
  Incomplete boxes*, and the refusal adds *and on the label's left* to the tests a
  square has to pass. A card printed with each set's box to the right of its label,
  which was read, is now refused, but its refusal does not say why. It names *all 8
  Forfeit boxes* as the ones it could not name, and 16 of the 22 boxes it does name
  are named by the wrong label: each Forfeit square as Retired, and each Retired square
  as Incomplete (filed).
- The reader's summary of a card refused for a Match Winner box, which the doctor
  prints above its verdict, no longer says the loss again. Under a refusal naming
  *both Match Winner boxes*, it printed *cross-check winner-tick: not printed - only 0
  Match Winner box(es) were located* and warned *only 0 'Match Winner' box(es) were
  located, so this card's match winner state may be incomplete*. The count is gone,
  and the Match Winner check is left out of the summary of a card on which one of
  those boxes is not named. The summary's checkbox line names each box it counts as
  not named, in the refusal's words, as in *and 2 of those not named: both Match
  Winner boxes*. On a card refused first for its header, that line is the only one
  that says which boxes are lost. A card whose Match Winner boxes are named keeps the
  check, refused or not.
- The warning for a card whose labels name more boxes than it needs says where each
  extra box is, in the words the refusal uses for a box: *the one more is a home
  Forfeit box in no set row, which no row reads*, *row 2's home Forfeit box again*, *row
  2's home Forfeit box twice more* or *the home Match Winner box again*. Every such
  card was warned *a tick on any box they do not name was not seen*. That is now said
  only of a card that draws a square no label names.
- A run that writes no dashboard heads the changes it found `changes a build would
  make`, as `--check` does. A run refused for a name it would have published, and one
  that could not write to `site/`, listed them under `since the last build`: the demo
  without round 5, given round 5 with `--players numbered` and a competition labelled
  with a player's full name, stopped with exit 2 under `players: no dashboard
  written` after three `new R5` lines that no dashboard showed. The paragraph under a
  fixture that has gone, printed under `--quiet` too, said "what this run wrote is a
  match short" on those runs and under `--check`, and the sentence saying why
  `--only` lists every season said the change would never be printed again. All
  three now say what a build would do, so a refused run and its `--check` print the
  same section. A write that stops part way, which writes one competition at a time,
  lists what it finished under `since the last build` and the rest under `changes a
  build would make`. With a card gone from each of three seasons and the second
  season's `season.json` read-only, it listed all three as made, and the build after
  it listed the second and third again.
- A dashboard left behind that prints players' names, which `--prune` refused to
  remove under withheld names, is counted once on the last line, by the names it
  prints, as the careers page already was. It was `1 dashboard(s) not removed, 1
  dashboard(s) still print players' names`, one copy counted twice, and a numbered
  copy refused beside it made that `2 dashboard(s) not removed`. The pruned section
  still names every directory kept and why.
- `--check` says of a dashboard or careers page left behind that a build would leave
  nothing under `site/` linking it. It said that nothing links it any more, over a
  front page it had not rewritten and that still linked it, in each of the six
  left-behind runs tried: dashboards, a careers page, both, under withheld names and
  in single-season mode. A build still says what the front page it wrote does.
- Every card is on the matches page, and each once. The page found a card by the match
  id it printed, which a card may not have and two cards may share. Measured on the demo,
  a round 2 card printing no id was left off `#/matches`, 14 cards and 56 rubber rows
  where the demo has 15 and 60, under a fixture reading `scheduled`, while the build said
  `all 15 card(s) scored`; with a second card for that night, neither printing an id,
  the clash was on no part of the page. A second card for a round 5 night under the first
  one's id showed one of the two twice in the front page's latest round and the other
  nowhere, and a card printing another fixture's id showed that fixture's card twice and
  itself nowhere. Each match in `season.json` now has a `key`, its fixture and its place
  among that fixture's cards, as `2/brindle-wombats/brindle-bunyips/1`, and each fixture
  and round lists its cards' keys in `keys`. The page finds every card by its key, and
  `#/matches` walks the fixtures alone, since every card is under one. Measured again,
  each card is shown once in all of those, at 1280 and 360 pixels. The key does not
  depend on the folder or the process, and a card for another fixture moves none: with
  the demo's round 1 Brindle Quolls v Brindle Bunyips card arriving late, 0 of the other
  14 keys changed, where a card's place in `matches` changed for 13. `id` is kept as the
  card printed it, and a night's own page is still `#/match/<id>`. The fields make the
  document format `3`: a page given a format `2` `season.json`, which has no keys, finds
  no card in it, and measured it showed 15 fixtures reading `scheduled` and said nothing
  more. It now says `This page reads season document format 3 and the data says 2` in
  its data health section, on every view.
- A night on two cards says which card was not counted, on the card and on each of its
  rows. Exported twice under two ids, a night read as two results: both cards `sets 4–0 ·
  points 6.4 / 3.4`, and 56 of the 64 rows in the table of every rubber `Played`, where
  the demo has 52 of 60. Exported twice under one id, the page showed the copy and not the
  card the ladder counted, and of a night two cards name differently the reading that was
  not used. Both cards are now shown, and the one left out of every total reads `copy of
  another card — counts for nothing` or `reading not used — counts for nothing`, as its
  rows do in the Night column: 52 of 64 rows `Played`. Neither says which file came
  first: the card kept is the one read first, and a browser's `name (1).pdf` reads before
  the `name.pdf` it copies. A level night and a final are not among them, since their
  rubbers are in the totals.
- A level card in a competition without draws says why its points are blank. Its summary
  on the matches page read `sets 2–2 · points — / —` and nothing more, and now goes on
  `level, and this competition has no draws — not in the ladder`, there, on the night's
  own page and on the team page. Its rubbers still read `Played`, because they are in the
  players' records.
- `--discover --force` keeps a sheet table you typed under
  `[competitions.<id>.expected]`. It wrote the file without it, exited 0, and the
  next run compared nothing, after a refusal that says everything you declared is
  written back. Each figure now comes back as the text it was read as: `12.0` stays
  `12.0` and `"10.90"` stays quoted, so the check compares what it compared before.
  The first run writes the table's heading for each competition, with every team's
  line commented out and no figure in it, to fill in and uncomment: typing the
  heading again below it makes a file TOML refuses. Uncommented as written, it is
  no table and no warning. A table no run could compare a line of, for want of an
  `after_round` or with every line refused, is still read as no table, so it comes
  back as the blank one; the refusal now names it, and sends you to the run that
  says what is wrong with it.
- `finals = false` on a round labelled as a final keeps it on the ladder. It was
  read as no line at all, so a declared draw with `SF` typed `finals = false` left
  the semi-final's results out of the ladder, and the run said it was declared as
  finals and offered the setting it had just ignored. The key is now read as true,
  false or not typed, and only a round with nothing typed goes by its label. The
  finals note says which rounds were declared and which were read off a label, and
  offers the setting only for the second. A value that is not `true` or `false` is
  refused, as `draws` is: `finals = "false"` had made a final. Because the setting
  now counts, a `[[…rounds]]` block typed for it, where no other block declares a
  regular round and no `rounds_total` is set, is a declared draw one round long: that
  run exits 1 on F3, which names the rounds to add, where it had exited 0 with the
  setting ignored. The note and the F11 warning, offering the setting where nothing
  gives the season a length, a draw off the cards or one of finals alone, now say so,
  and to give every round a block or set `rounds_total` as well.
- The `--discover` refusal names each round whose declared `finals` goes with the
  draw. Finals are declared only in the `[[…rounds]]` blocks `--force` drops, so a
  round 3 typed `finals = true` came back a regular round, and the ladder moved,
  with nothing said but the dates, fixtures and byes. The refusal now says the run
  after it tells a final by its label alone, and which rounds that moves, each way.
  It names a round's `note` among what the blocks hold, too.
- A fill-ins heading worded `Reserves` or `Substitutes` ends both squads as `Fill-ins`
  does, printed over one squad or both, singular or plural, alone or with a side
  named, and the card says nothing of it. Over one squad only, or a different word
  over each, a plain heading is neither a pair printed for both teams nor one line
  printed over both, and holds nothing a name cannot, so no rule for a heading worded
  differently ended the squads at it: it read as a player in the squad it headed, and
  a fill-in printed under either column was registered to that team. Measured on both
  legs of one fixture with a fill-in under the home heading, at exit 0: `Reserves`
  over the home squad only gave 8 players where there are 7 and 5 warnings where the
  template's own headings give 3, over the away squad only 8 and 6, and `Substitutes`
  over home beside `Reserves` over away 9 and 8, with `player-two-rosters` and
  `roster-incomplete` among them. With the fill-in under the away column instead, 8
  and 6, 8 and 5, and 9 and 8. With no `Comments` heading, a heading over one squad
  only lost both squads. Each now reads as the template does. Two unrated namesakes
  level at the foot of both squads who sat the night out are players again over any of
  those, and over `Reserves` printed over both squads, where the rule for a line
  printed the same over both had taken them for a heading: of the 22 namesake cards
  measured for that rule, 18 now keep both people, where 14 did. The words are whole,
  so a comment's `reserved` or `substituted` is not one. The cost: on a card with no
  `Comments` heading, a comment with the word `Reserves` or `Substitutes` in it,
  singular or plural, now ends the block as one with a word beginning `Fill-in`
  already did, so a heading in another word printed over both squads, `Emergencies`,
  is read as a player in both, with no note. On both legs that season has 7 players
  and 2 warnings where it had 6 and 1. The same card with `Reserves` over both squads
  and a comment beginning `Fill-in` now reads as the template does. Troubleshooting
  entry: *The report says a card's roster was not read*.
- A `-` printed under a fill-ins heading nobody filled in no longer keeps the heading in
  both squads. A line with no letter in it ends a squad, and the rule for a line printed
  the same over both squads was asked only when nothing had ended either, so with a `-`
  under each copy of `Emergencies` or `Fill Ins` printed over both squads it never was.
  Measured on both legs of one fixture with nobody filling in, at exit 0: 7 players
  where there are 6, the heading on both teams' squads, a `player-two-rosters` beside
  the one warning the template gives, and each card's note quoting the two dashes as
  headings. With a fill-in for the same team on each leg and the `-` under the other
  copy, 8 players and 4 warnings where the template gives 7 and 5, the fill-in
  registered to the team. A squad that ended at such a line has not ended at a heading,
  so the rule is now asked, both seasons read as the template does, and each card notes
  the heading and not the dash. Where the rule finds nothing, the `-` still ends its
  squad and is quoted. The cost is the rule's own: two unrated namesakes level at the
  foot of both squads who sat the night out, over a `-` under each, now end the squads
  there as they do with no `-`, and the card says so; before, the `-` kept them, and
  over `Emergencies` the heading too. Troubleshooting entry:
  *The report says a card's roster was not read*.
- A `[player_names]` line that gives a misspelling the name the right spelling
  already has is now said on every run, `--quiet` included, with the `[aliases]` line
  that joins the two. `"bo-nakamora" = "Bo Nakamura"`, for a card misspelling
  `bo-nakamura`, showed one person as two players called Bo Nakamura, and a `--quiet`
  rebuild printed the 8 lines an empty `config.toml` prints. The line is offered where
  a roster block registers just one of them, the spelling it joins them under, and
  names the `[player_names]` lines to delete. Two registered players given one name,
  or none, are offered no line, since the cards do not say which spelling is wrong.
  Nor is a player a `[namesakes]` split made, since an alias is applied before the
  split and one keyed on that player reaches nobody. Each competition's page is
  judged, and in multi-season mode the careers page too, where two seasons' fill-ins
  spelt two ways and given one name made two careers under it and nothing was said.
  One pair on several pages is said once, and a name spaced another way is the same
  name. The players a split made, who share a name on purpose, are not said at all.
- A fill-in is shown by their set row's spelling. The name was made from the key, so
  `Tui-Mae O'Dowell` showed as `Tui Mae O Dowell`, a `Ros D'Arcy` lent by another
  grade as `Ros D Arcy` there and `Ros D'Arcy` at home, and a fill-in `[namesakes]`
  split as `Pim Arkwright Sole Hilltop Aces`. A fill-in printed two ways is named as
  the first card read spells them, the rule a roster name follows, and a washout's
  set rows are read too, since the page shows who they name. A spelling `[aliases]`
  folds away now gives way to a later card's that it does not fold, for a roster name
  as well: a roster block misspelling Bo Nakamura on the first card read named him
  `Bo Nakamora`, for all the alias. A shouted set row is shown as a shouted roster
  block is, so `BO NAKAMA` is `BO Nakama`, where it was `Bo Nakama`. With `--players
  initials` the label follows the spelling: `Tui-Mae O'Dowell` is `T.O.`, where `Tui
  Mae O Dowell` was `T.M.O.D.`. The demo's dashboard is byte for byte what it was:
  its fill-ins are all registered with a team of their own.
- A `[player_names]` line under a name `[namesakes]` splits among fill-ins alone is
  now said, with a line for each player the split made, as it already was where the
  split's players were registered. The line did nothing, and a `--quiet` rebuild
  printed 8 lines and nothing about it. The player a split leaves on the name is
  now named with every team a set row prints them for, a washout's included, and not
  with their roster team alone.
- A `[namesakes]` entry for a team whose cards print the name only on a washout, or on
  a night not yet played, is now said to wait there for that player's first card that
  counts. The note said no card puts the name in that team and to check the team
  against its banner, which the washout's roster block contradicts.
- `tools/doctor.py` calls a page a picture of a card for the image it paints, and no
  longer for what it lacks. The reader recorded no image, so a page was called a picture
  when it painted no rectangle and found no header field but the date and the time. A
  team list, a rules sheet with a start time in it and a fixture list, each typed out,
  were told they were *an image of a card rather than a card*, and of the pictures only
  a PNG that Chrome printed from a web page was told so. The fixture card rasterised by
  Ghostscript, and a page that is one image and nothing else, were told the page is
  empty. A card's PNG that Chrome printed to PDF was told its table's vertical rules are
  absent, for the rectangle or two Chrome paints behind the picture. And a scan with the
  card's words laid over it was told its shapes are drawn another way. The reader now
  records the box each image is painted into, an image drawn by `Do` or an inline image,
  however its own matrix, the page's `/Rotate` or a form it is painted in turns and
  places it, and the report prints an `images` row saying how many a page paints and how
  much of it they cover. A page whose images cover a tenth of it or more, and that has
  no table rules, is called a picture, and is told where it belongs if it is a draw or a
  team list with a photograph on it. A club's logo on the letterhead, 60 by 30 points,
  covers under 1 per cent of the page and makes no page a picture, and a page typed out
  is told that it is one. A card drawn in paths is no longer told that a scanning app
  may have made it, since such a scan is now told it is a picture.
- The reader no longer reads an inline image's bytes as the page's content. A `(` among
  them opened a string that swallowed the rest of the page, so a team list with a small
  inline image between its heading and its names read 3 of its 9 words. It now reads
  all of them.
- Under `--redact`, the doctor gives the causes of a refusal in its own words. The whole
  report was scrubbed against the page's words, the advice with it, so a draw typed out
  was told that a program saving a card again "can <4 chars> them so", a rules sheet
  that it was an image of a card "rather <4 chars> a card", and a card turned upside
  down by `/Rotate`, whose comment said the court lights were turned on late, to "keep
  <2 chars> in a folder", and that the page is "<6 chars>". The causes, and the rotation
  and images rows they quote, are now published as they are written, as the footer is,
  since nothing in them is read off the page. The reason and the detail above them are
  still scrubbed: the detail can quote the page, and no reason does yet but one could.
  So a word of the reader's own in them can still be shaped (filed).
- A season's progress counts its regular rounds, as its length does, and names the
  finals played on a line of their own. Every round with a card was counted, so a
  draw of two rounds and a final with cards for all three read `Round 3 of 2`, its
  bar full, in the report, `season.json` and the front page, and a first run over
  the demo with a semi-final and a grand final said `5 rounds`, offering
  `rounds_total = 5`. They now read `Round 2 of 2` with `Finals played: Round 3.`,
  and `3 rounds` with `Finals played: SF, GF.`, and every place that says the count
  reads it from one function: the report, `season.json`, the front page, the review,
  the comment `--discover --force` writes and discovery's notes. Discovery's note on
  a season of unknown length had counted the draw's rounds instead, so a week the
  byes table rests the whole grade in, or a final the draw lists that nobody has
  played, was a round played, and the note said `"4 rounds played"` where the page
  said 3. Its note on a season with a length now says `3 regular rounds (1, 2, 3)
  and 1 finals round (SF)`. A season with cards for its semi-final alone reads `0
  rounds played`, where it read `1 round played`, and its box names the semi-final.
- A run stopped by F3 because the season was declared shorter than the regular
  rounds already played builds its page as a season of unknown length. With
  `rounds_total = 1` over three rounds of cards, the report said `3 of 1 played` and
  the page `Round 3 of 1` and `100% of the declared rounds` beside `1 validation
  error`. The page now shows `3 rounds` and no bar, and says that more regular
  rounds have been played than the season was declared to run, and the report
  says `3 played; the season was declared shorter than that (F3), so no percentage
  is shown`. `season.json` gives the season no length and still marks it declared.
- One round of cards is `1 round played` wherever the build says so. Discovery's
  note, the review and the comment `--discover --force` writes said `1 rounds
  played`, where the front page already said `1 round played`; all four now form
  the words the same way.
- The review says of every value, on every run, whether `config.toml` has it live,
  behind a `#`, or not at all. Only the run that wrote the file said where each
  value was. The run after it asked again for all 6 of the demo's values, "Add
  these to config.toml", of a file that held the 5 it could write behind a `#`. The
  run that wrote it was guessing as well: "commented out" meant only "not live",
  and it said so of 8 players' spellings the file holds no line of. The file is now
  read as it stands, values and not only keys, so a line you changed behind its `#`
  is "not in it" rather than the value offered. A run with no file to read, such as
  `--check` before the first build, reads it as empty, and says of the draw that it
  is for the report only.
- A value that is not in `config.toml` is told where its lines go. Its lines begin
  with the table they belong in, and added as printed where the file had that table
  already, `[player_names]` for 8 players' spellings or the demo's
  `[competitions.wednesday-division-3]` once its `# season` line was taken out, the
  next run stopped at exit 2, "Cannot declare ... twice". So did a file written
  before any card, given the team list, the season length and the season name, each
  opening the same table. A value whose table the file has is now told to add its
  lines to that table, and values that share one the file lacks to share it. The
  offer to fill in such a file says how many of the values below the rewrite writes
  behind a `#`: it said each value "above" would, of the list below it, the draw and
  players' spellings included, which no rewrite writes.
- The match format is offered as the line that declares it, in the file and in the
  review alike: `format = "<name>"` for a shape with a name, and `slots = [...]` for
  one without. Two doubles and a singles was offered `format = "d1-d2-s1"`, which
  the next run refused with "unknown match format", exit 2. The file's `# slots`
  line is now your cards' own shape. It was `["doubles", "singles", "singles",
  "doubles"]` whatever the cards, and deleting its `#` refused all 15 cards of a
  doubles-2 season and all 6 of the d1-d2-s1 ones. No format names were added.
- The draw read from the cards is listed in the review for checking your
  association's draw against, and is no longer offered for adoption. The review said
  it was in `config.toml` behind a `#`, and what the file held there was an example
  round 1 dated 2026-08-18 pairing your first two teams: deleting every `#` the
  review pointed at ended the next run with 3 draw-mismatch errors, on each of five
  demo formats. The cards' own draw is no safer to declare. Pasted in, a season of
  three rounds stopped the run with F3 the week round 4's cards arrived, while no
  `rounds_total` was declared, and a round with one card held back stopped it with
  draw-mismatch when that card came in, whether `rounds_total` was declared or not.
  The file now says in words how to type in a published draw, and writes no round a
  deleted `#` would declare.
- A card the build refuses is no longer counted as one of a competition's own errors
  anywhere. Every competition's document names every refused card as an F4 error, since
  a refused card could belong to any division. The report's last line counts each
  refusal once, but four places still counted them as the competition's errors beside
  the count of cards not read: each competition's own `problems` line in the report, the
  front page's red flags, the first pill of its data-health row and the summary of the
  data-health detail under it, which opened on load for any error. Measured on the demo
  with two PDFs refused, one cut to its first 1,024 bytes and one made unreadable, the
  own line read `2 error(s), 6 warning(s) -- see below`, the front page flagged `2
  validation errors` beside `2 cards could not be read`, and the pill read `✕ 2
  validation errors` beside `✕ 2 cards seen and not scored`, above an open `Data health
  detail (2 errors, 6 warnings)`, for a season with nothing else wrong. Now the line
  reads `0 error(s), 6 warning(s) and the run's 2 refused card(s)`, the front page flags
  only the cards, the pill reads `! Validated with 6 warnings`, and the detail, which
  lists every error as before, reads `Data health detail (0 errors, 6 warnings and 2
  cards not scored)` and stays closed until one of the competition's own errors opens
  it. A season whose only problem is a refused card reads `✓ No errors in the cards
  scored`, not `Validated clean`, beside the red pill for the card, above a closed `(0
  errors, 0 warnings and 1 card not scored)` where it read an open `(1 error, 0
  warnings)`. In a three-division run with one draw error, the own lines read 0, 1 and 0
  error(s) where they read 2, 3 and 2, so they now add up to the last line's `1
  validation error(s)`, and only that competition's front-page card flags `1 validation
  error` and only its detail opens. The build counts which errors are a competition's
  own once, and the document carries the number as `validation.own_errors` for the page.
  A page opened on a document built before this counts every error, as it did.
- The report and the page print what a finding is about only where its message has not
  already begun with it. Nearly every finding names its card, match or player at the
  start of its message, as `data/locked.pdf: not a readable PDF`, and the report printed
  `subject: data/locked.pdf` on the line under it while the page's data-health list put
  `[data/locked.pdf]` after it. Of the 1,171 findings the tests make that name what they
  are about, 1,060 began with it. The demo's report with two refused cards is 6 lines
  shorter, and a four-division one 18. A subject the message does not begin with is
  printed as before, such as `R1 lakeside-spinners v parkview-wanderers` under a
  `card-missing` warning that begins `round 1:`. Every finding in `season.json` keeps
  its `subject`, for a program to find it by, and says in `subject_said` whether its
  message already did. A build that withholds names asks again of the message it writes,
  so a message withheld whole still shows the card it was about.
- A line printed for you to paste is quoted so that the shell reads nothing inside
  it. A name that needed quoting went in double quotes, inside which a shell still
  reads `$`, a backtick, `"` and `\`, and an interactive bash reads `!`. So a folder
  named `o$x` pasted to a build that read another folder, one named with a `"` or
  ending in `\` pasted to `Syntax error: Unterminated quoted string`, and `club!2026`
  pasted into bash gave `event not found`. Nine folder names were tried in the
  demo's `next` lines, the build's `cd` and `--discover` hints and its
  `tools/doctor.py` line, and seven letterheads in the fetcher's `--letterhead`
  offers. 20 of the 52 lines failed or named something else, and all 6 with `!`
  gave `event not found` in an interactive bash. Now a name is put in single
  quotes, as `'our season'`, with `'\''` for a quote inside it, and each of the 52
  runs as printed, in bash as well. The cron line and the report header of
  `tools/scheduled.py` quoted with Python's `shlex`. They now use the same rule, so
  an apostrophe there is `'\''` as well, where it was `'"'"'`. On Windows a name
  still goes in double quotes, because neither cmd nor a Windows program reads a
  single quote: in single quotes, a club's name of four words offered as
  `--letterhead` would reach Python as four words. The cron line is quoted for a
  POSIX shell whichever machine prints it.
- The build's `cd` hint, printed for a run from outside the project, now carries a
  `--out` or `--config` you gave as what you meant. It carried them as typed,
  after the `cd`, so `--config club.toml --out mysite` run from your own folder
  pasted to a build that wrote its 9 files into the project's `mysite`. It also wrote
  a new `club.toml` there, and it never read yours. Each is now named in full, or
  from the project when it is inside it. One you typed is carried even when it names
  the default, as `--out site` or `--config ./config.toml`, which the hint had left
  out, so that the pasted build wrote the project's `site/` in place of yours.
  `--data` is still carried as typed, because the line is there to read the
  project's own cards. A folder whose name begins with a dash keeps its `./` in that
  hint, in the `--only` hint, in the `--discover` line and in the `tools/doctor.py`
  line for a card the build refused. There, `./-cards` was printed as `-cards`, and
  pasted it gave `argument --data: expected one argument`, or from the doctor
  `the following arguments are required: FILE`.
- `tools/fetch.py` now also says that the cards are going straight into `data/` when
  `--into` is a link to `data/`, or is the folder `data/` links to. Before, it
  checked the folder as typed while its `next` line resolved it, so the `next` line
  was right and the note was missing.
- A player's page rates each set slot the way its Win rate tile rates the player: sets
  won out of sets decided. The slot table worked out a rate of its own, sets won out of
  sets played, so a retired set counted against the player there and nowhere else. In
  the demo season the four players in round 4's retired doubles were shown 50.0%,
  40.0%, 33.3% and 40.0% at d2, for records of 66.7%, 50.0%, 50.0% and 50.0%. A player
  whose one set was retired was shown 0.0% under a tile showing no rate at all. Each
  slot in a player's `by_slot` in `season.json` now carries a `win_pct`, from the same
  rule as the player's own, and the page prints it. A slot with no set decided shows
  a dash, which Copy and CSV leave as an empty cell. A `season.json` built before this
  has no such field, and its slot table shows a dash in every row rather than a figure
  of the page's own. The column now says what it is out of in the table's key: "Sets
  won as a percentage of completed sets". In every competition, the next build
  rewrites `season.json`, `season-data.js` and `app.js`, and with `--single-file` the
  folded page, and nothing else.
- A `[seasons]` label keyed with capitals or a space, `"2021-Winter"` or
  `"2021 winter"`, now labels its season, and a key that names no season is reported.
  The label was looked up under the key as typed, while the season's folder is read
  with capitals, spaces and punctuation folded, so either key labelled nothing, and
  the build said nothing: the label was on no page. The key is now read the way the
  folder is, and the site is byte for byte the one the lower-case key builds. A key
  no card is filed under, such as `"2021-wintr"`, is a `config` warning, printed
  under `--quiet` too, which asks about the nearest season no line labels yet. A line
  for a season still to come, one with its folder under `data/` or a section's
  `season_key` and no card yet, is only a note that the label waits for its first
  card. Two keys for one season give one answer whatever order the file lists them
  in: the one spelt as the season is, else a label both agree on, else none, and a
  warning says which. `--discover` no longer writes a commented line for a season a
  folded key already labels.
- A fixture gone from a dashboard whose `season.json` holds something other than a
  file's name for it is said in the same sentence as one that holds nothing. Only a
  `source` that was missing or blank got that sentence, so a `source` of spaces printed
  `last read from` and stopped, and so did a tab or a no-break space. A list, a number,
  `true` or a table was printed the way Python writes it: `last read from
  ['data/demo-season/round-02/wombats-v-bunyips.pdf']`, `last read from 5`, `last read
  from True`. Each now reads `and the last build recorded no file for it`. A build
  writes a card's path there, so only a document edited by hand or written by something
  else gets this far, and a path prints as it did.
- A team printed with the letter A keeps it, and so does a player printed with the
  initial A. A shouted name is put in title case with `a` among its small words, so
  HILLTOP A beside HILLTOP B was `Hilltop a` beside `Hilltop B`. The front page said
  `Hilltop a lead on 6.33`, and so did the ladder, the report and the `config.toml` a
  run writes. DIVISION A was `Division a`, THE A TEAM `The a Team` and HILLTOP A (W)
  `Hilltop a (W)`. An `A` now stays a letter where it closes the name, before a number,
  a bracket or anything else that does not begin with a letter, after `the`, `a` or
  `an`, as in AN A TEAM, and in a name printed in mixed case. Inside a phrase it is
  still the article, so KINGS OF A COURT is `Kings of a Court`. A player with the
  initial A was shown with a small `a`, as a spelling the build had worked out. So
  every player's page, not only that player's, was flagged `part derived`, and the
  review list offered the name as one with no printed spelling. The name is now shown
  as its cards print it, and nothing is flagged. Not every name is right yet: WORTH A
  1000 WORDS is now `Worth A 1000 Words`, its count read as a team's number is.
  A name with no `A` of its own is shown as before, and so is the demo season.
- The paragraphs behind the rules banner's *What these mean* now share their words out
  across their lines, so none ends on a word or two of its own. At the 60ch cap, the
  scoring explanation under `draws = false` ended on `sets` alone. On a phone the demo's
  format ended on `6)`, and 24 of the 100 paragraphs the banner can show ended on a line
  of 15 characters or fewer. With `text-wrap: balance`, none does, in any of the four
  faces, at 1280, 700 or 375. Each keeps its number of lines, so the opened banner is
  the height it was, and no line is longer than it was. A browser that does not balance
  lines wraps them as before. The 60ch cap stays. Even balanced, 65ch left 11 of the 100
  with a line over 80 characters, and 61ch left two in DejaVu Sans.
- A card whose round is printed as a superscript or circled number, `³`, `③` or
  `¹²`, is built. Python calls those characters digits, 128 of them that `int` will
  not read, so the build stopped with a traceback before it wrote a report or a site,
  and `tools/fetch.py` stopped the same way on an index listing such a round. Such a
  label is now a round with a name, like `Catch-up`: it comes after the numbered
  rounds, is scored as a regular round, and has the warning for a round label that is
  neither a number nor a finals label. `３` and `٣` are still round 3, as before. A
  label of more than 4300 digits, which `int` will not read either, is a name too. It
  stopped the build the same way, and a bye of that many digits in `config.toml`
  stopped `--discover` as it wrote the file back. A semi-final printed `SF３` is now a
  final, as `SF3` is, where it was scored into the ladder as a round with a name. The
  sort, the finals, that warning and the byes written back ask one test of a label,
  so none of them calls a number what another calls a name.
- The round a card is filed under is decided by one function, which the build, the
  reading of `config.toml` and `tools/fetch.py` all ask, where each had its own copy
  of the rule. The copies agreed, so nothing any of them prints changes. What changes
  is that they cannot drift apart: the fetcher's count of a round and the declared
  draw's rounds and byes follow the rule wherever the build does.
- A build that withholds players' names spends less of its time on it. It read every
  competition it was about to rewrite four times over, and five where a card left a
  line out of its squads: three or four times to learn the names and number the
  players, and once to check what it wrote. The learning is now done in one reading,
  and the check still reads what the build writes for itself. Timed on the demo,
  withholding took 11% of a numbered build and now takes 7% to 8%, 12% to 14% where a
  card left a line out and now 7% to 9%, and on the 10-team demo 7% to 8%, now 5% to
  6%. Every file written is the same, byte for byte, and so is a refused build's report.
- A warning whose message opens with a card's path fits the report in a multi-season
  run. The path followed the competition id on one line, and with the season's key in
  the id, ten demo seasons each with one card printing `VS` for its result printed ten
  warnings of 89 columns. A first word that cannot share its opening's line now starts
  the next one, and the rest of the message follows it there, so the id stands alone
  above the path. It is the report's rule and not the warnings' own. A note or a
  sentence that opens with a word too long for its line is printed the same way, in
  `tools/fetch.py`'s report too, whatever the word and even when a folder's name has a
  no-break space in it, so a wrapped line past 78 columns holds one word and nothing
  else. Under a very deep `--out`, `open` now stands above its path. A plain field is
  not wrapped yet: under a deep enough folder, `source`, `wrote` and `file` still print
  their path behind the label.
- The footer under a run's errors, which asks you to quote the F-number, is wrapped at
  the report's width. It was one line of 122 columns, and with the demo's paths the
  only line past 78 in a run that refuses a card, plain, with `--check` or with
  `--quiet`.
- A card that counts for nothing is named once, by `no-result`. A night called off
  after two rubbers, its card printing `DRAWS WITH` as Match Centre prints a cancelled
  card, failed the run with 2 errors beside the warning that it was recorded as
  cancelled: F6, saying the verb disagreed with the set rows so the card's numbers
  were not trustworthy, and `verb-no-result`, saying only 2 of the 3 sets a result
  needs were complete. A night not yet played, its card printing `VS`, drew a
  `verb-absent` warning saying its set rows were still read and scored, when none of
  them was. Now a card with Match Cancelled ticked, and neither Match Forfeited box,
  is held to nothing it prints beside its rows, by F6, the TOTAL row checks or any
  verb check. That washout now builds with exit 0, the warning that it was cancelled
  its one finding, and so does the same card on the second page of a file. Of 15
  washouts tried, 10 failed the run and 3 more drew warnings beside `no-result`, and
  each is now `no-result` alone, among them one printing `LOSES TO` over a home win,
  one printing a word this program does not know, which was `verb-unknown`, and one
  printing `DEFEATS` and ticking home over rows left blank. A card read 0-0 on every
  row with no box ticked is still read for how it was read, since blank rows are what
  a misread card looks like too: its verb, tick and TOTAL row are held to its rows,
  and a word in its banner nobody knows is still `verb-unknown`. Only the checks that
  hold a verb to a counted result pass it by, so the night not yet played is
  `no-result` alone, one printing `DRAWS WITH`, which failed on `verb-no-result` with
  or without a TOTAL row, now builds, one printing `DEFEATS` and ticking home fails on
  its 2 F6 errors alone, and one whose every rubber is ticked `Forfeit` against both
  sides loses the `verb-mismatch` beside its F6. A forfeit with Match Cancelled ticked
  as well counts, and is checked, as the forfeit it is read as, so one printing `VS`
  now gets the `verb-absent` warning the Cancelled box silenced before.
  `tools/doctor.py` tells a washout's owner the same, where it said the build reported
  a disagreeing cross-check as an error, or warned that none was printed.
- The byte-identical rebuild test holds the order of the lists two more findings print.
  The demo season it builds puts no player on two teams' cards in one round and no two
  names in a one-place row, so with the teams `player-two-teams` lists taken from a set,
  in its sentence or in its detail, or the names `set-player-count` quotes from the
  card, every build it compared came out the same, whatever hash seed the suite ran
  under, 100 of them tried. The error's two lists were held only by a test of the
  sentence they are listed in, which passed under 20 of those seeds anyway, and the
  warning's names by nothing. The card of the team that lent a player now names them
  too, a doubles pair is typed into a singles row as well, and each of the three lists,
  taken out of order, fails the rebuild test whatever seed the suite runs under. It
  also asks for the pair each card was given in the order the card prints it, because
  a list sorted either way holds as still as the card's, and for every build to reach
  the last line of its report with no traceback, because a build that raises exits 1
  as well. The squad `roster-mismatch` lists, which no card can reach, is held in a
  process of its own under two seeds that put it out of order, from a team that lists
  it in reverse, and the test of a rubber with two names in one place asks for its
  warning whole, with the names in the order the card prints them. The demo, the
  reports and the pages are unchanged.
- A careers page too big for one page is split by initial. The page grew with the
  club and nothing said so: 30 demo seasons of 12 teams made one `careers.html` of
  1,537,058 bytes for 462 careers, past the spec's 1.5 MB, and the report's
  `careers` line gave the count and nothing else. Once the careers would come to
  more than 1.5 MB as one page, `careers.html` is now an index of every name, and
  each initial has a page of its own, `careers-a.html` to `careers-z.html`,
  `careers-0-9.html` and `careers-other.html`. Over those 30 seasons that is 29
  pages, 1,760,661 bytes in all, the largest of them T's page at 142,388. The names
  are fixed, so no part of anybody's name is ever in one. An accent is set aside, so
  Émile is under E, and any other letter, such as Ø or Ł, is under Other. A player
  page's link to their career still lands on it, opened from a folder or from a web
  server: a short script on the index follows the name's own link on, and with
  JavaScript off the link lands on the name, marked, one click away. The report's
  line says what one page would have come to, as "As one page it would be 1.6 MB,
  over the 1.5 MB the page is split at", and says nothing more about a page that
  fits. Every page of a split is measured too, and the line names the largest still
  more than 1.5 MB on its own, as "Even split, careers-t.html comes to 1.6 MB, over
  1.5 MB": the largest initial's page, with the figures, gets there by itself
  at about 5,000 careers. A site once split stays split. Left behind, the pages are
  listed once, as a careers page of 29 pages, and `--prune` removes all of them or
  none. With `careers.html` deleted they are listed under `careers-a.html`, and the
  run's last line names that file too, where it named a `careers.html` that was not
  there. A file of yours under any of the 29 names stops a multi-season build that
  prints names before anything is written, whether tonight's page is split or not. A
  page that fits is the page it was, with a longer comment in its head.
- `tools/demo.py --seasons multi` writes two seasons of the fictional club, so the
  careers page can be seen before a club has two seasons of its own cards. The demo
  wrote one season, always labelled "Season 1", so the only way to see the page was
  to run it twice by hand into two season folders. That gave two seasons with one
  label, the same dates and the same match ids, and 18 of 18 careers spanning both.
  Now "Season 1" and "Season 2" go into `demo-seasons/2026-summer/` and
  `demo-seasons/2026-winter/`, with dates and match ids of their own. Between them
  one player leaves the Magpies and a newcomer joins, so 17 of 19 careers span both
  seasons and two are one season each. The demo also writes `demo-seasons.toml`,
  which turns multi-season mode on, and prints the line that builds with it:
  `python3 build.py --data demo-seasons --config demo-seasons.toml`. Your own
  `config.toml` is not touched. The first season is the plain demo's, byte for byte,
  and without the option the demo writes what it always did. `--out` must be a folder
  of the seasons' own: `data/`, anything inside or around it, and a folder holding
  cards the demo did not write are refused before anything is written, because the
  line builds all of the folder without your `config.toml`. Measured, a club with a
  season of its own in `data/` and `players = "numbered"` ran `--out data`, pasted
  the line, and had its players named in three files of `site/`; one level inside
  `data/`, the two seasons built as one, with 15 matches claimed twice. On an odd
  draw the note on byes names `demo-seasons.toml`, the config the line reads, and
  `.gitignore` names both things the demo writes beside `build.py`.
- A list of three in a sentence reads `a, b and c`, where six sentences said `a and b
  and c`. Three cards that claim one night and disagree about its games were told
  `24-6 and 24-7 and 24-9. So neither is counted ... because choosing between two
  results is a guess this program will not make`, and are told `24-6, 24-7 and
  24-9. So none of them is counted ... because choosing between them is a guess`,
  and to remove every file that is wrong where they were told the file. Three that
  agree about the result and name different players were told `both cards say the
  same about it`, of a rubber `the other card` gives to somebody else, and that the
  night comes back `when the card that is wrong leaves data/`. They are told `every
  card says`, `another card` and `when every card that is wrong has left data/`. Two
  cards are still `neither`, `both` and `the other`, but are told `choosing between
  them` where they were told `choosing between two results`. The sets and the status
  of such a night are listed the same way, and so are the teams the draw has a
  resting team playing, `HOLLOW WRENS, IRONBARK OWLS and JUNIPER QUAILS`, and the
  copies `tools/fetch.py` keeps a new card beside. Every other sentence of one or two
  reads as it did.
- Thirteen more lists in a sentence that were joined with commas alone read as
  sentences too: the cells a TOTAL row gets wrong, the sets no player was read for,
  the teams whose roster blocks print one name, the players with no rating, or not
  in the registered squad, or missing from the roster, a round's unaccounted teams,
  a `[namesakes]` entry's teams wherever its notes list them, cards of two shapes, a
  careers template's missing markers, and the header fields `tools/doctor.py` finds
  missing. `no rating was read for bo-nash, dee-ferro, eli-quade.` is `... for
  bo-nash, dee-ferro and eli-quade.`, and two are `aces and owls` where they were
  `aces, owls`. Where the sentence offers a choice it says `or`: `[rules] unplayed
  must be 'average' or 'zero', got 'avg'`, where it said `must be one of 'average',
  'zero'`, and a section without its season key in multi-season mode `needs
  season_key — 2025-winter or 2026-summer`. A finding's detail line is a list and
  keeps its commas. The doctor's `That line` starts the next line, so a header short
  of all three fields keeps to 78 columns, where it came to 82.
- A name that is nearly a team or a setting is asked about the nearest one, and about
  two only when they are equally near, as the README says. F8, the loader, and
  discovery's note with the page's warning, which share one question, each asked about
  every name within two edits, in three wordings. With Hilltop A and Hilltop B
  declared, a card bannered HILTOP A was asked `Did you mean one of 'Hilltop A'
  (hilltop-a), 'Hilltop B' (hilltop-b)?` by the note and the page and `Did you mean
  hilltop-a, hilltop-b?` by F8, though it is one letter from Hilltop A and two from
  Hilltop B. It is asked `Did you mean 'Hilltop A' (hilltop-a)?` now, and `Did you
  mean hilltop-a?` by F8. A banner that differs from a team only by a number in the
  same place or a tag in the club's own scheme, such as HILLTOP beside Hilltop A and
  Hilltop B, may be another side of the club, and F8 asks nothing of it now, as the
  note asked nothing. Neither asks it about another club instead. A line
  of the results sheet keyed `Hilltop` is still asked about both sides: it is copied
  off the competition's own sheet and so is one of them, where a banner may be a third
  side's card filed in the wrong place. A setting typed `lets` is asked `Did you mean
  sets?`, where it was asked `Did you mean one of sets, slots?`. A tie is asked the
  one way everywhere, `Did you mean sets or slots? Key the line on the one it is
  for.`, and without the advice about a banner, which is no line anybody keyed.
- A fixture gone from a dashboard whose `season.json` holds, where the file's name
  should be, only characters that do not print is said in the sentence for one that
  holds nothing. A zero-width space, a byte-order mark, a word joiner, a soft hyphen
  or an escape on its own passed for a file's name, since none of them is
  whitespace, and the line read `last read from` over nothing that shows. Half a
  surrogate pair on its own stopped the report: the build wrote the dashboard,
  printed nothing and exited 1. Each now reads `and the last build recorded no file
  for it`. A path with a zero-width space inside it prints as it did, and one with
  half a surrogate pair inside it still stops the report. A card still in `data/`
  whose row was edited like that has not gone, and the next build writes its row
  again and says nothing about it, as it says nothing about a card renamed.
- A card that prints a figure in digits `int` cannot read, such as a superscript `³`
  from its font's own mapping, is scored. One such court number, games figure or TOTAL
  cell refused the whole card, `the page could not be read`, and the build left the
  night out and exited 1. A games figure printed so reads as 0 and the card's warning
  quotes it, `row 1: the home games figure printed, '³', is not a number, read as 0`,
  as a blank one reads as 0. A TOTAL cell printed so is not read, and the warning
  names it, `the card's TOTAL row prints '³' as the home sets, which is not a number,
  so that figure was not read`, while a TOTAL games figure left blank beside it is
  still warned about. Either warning quotes the whole cell, and any text in it that is
  no figure: a games cell printing `-` or `ret`, which was told no figure was printed,
  is quoted, and a TOTAL cell printing `-` or `3.`, which went unmentioned, is named.
  A court printed so is unknown, as a missing one is. A season key of more digits
  than `int` reads orders that part as text: a section whose `season_key` was 4301
  digits stopped the build with a traceback, and builds. The reader, the season order
  and a round's number ask one test of a figure.
- A retired or incomplete set is nobody's in a card's own cross-checks, as it is
  nobody's in the ladder. The checks credited it to whoever led when it stopped, so a
  card whose finished sets went 1-2 and whose last set retired at 5-4 was told its
  rows give `at most 2-2 sets (level)`, and its `LOSES TO` and its tick for the away
  side, which the finished sets agree with, were two F6 errors and exit 1. Seed 4 of
  `tools/demo.py`, and seeds 30 and 34 with `--teams 12 --rounds 1`, built so. The
  rows give `at most 1-2 sets (away)` now, and a TOTAL row that counts the stopped set
  to its leader is the error instead, `home sets 2 printed, but the rows allow at most
  1`.
- Under a typed-in draw, the sheet warning no longer asks for a card from a round the
  draw gives the team no match in. With round 7 of the draw leaving a team out and no
  round 6 in the draw, the entry read `R6 nothing read; R7 nothing read` and the
  advice `If it played those weeks, those cards have not been exported`. A round whose
  typed fixtures leave the team out now reads `R7 no fixture`, a rest the draw or the
  byes table states reads `R7 bye`, and either is a week off, so the advice is `It had
  round 7 off: check that week against the sheet's byes first, then its matches`. A
  round number the draw skips, before the last numbered round with fixtures typed,
  has no entry, or reads `no fixture` where it is the sheet's own round. Past that
  round, and under a draw read off the cards, a round nobody has a card for still
  reads `nothing read`, as does a round whose typed fixtures name a team the
  competition does not have, since that name may be this team's, misspelt. A rest in
  the byes table for a week nobody has a card for is `bye` under a draw read off the
  cards too. It read `nothing read`, and under `unplayed = "average"` the advice was
  to write the rest in the byes table it was already in.
- A `[namesakes]` entry that tells nobody apart, or has a team no card prints the
  name under, is said on every run, `--quiet` included, where it was a note `--quiet`
  drops. `bo-nakamura = ["riverside-rockets", "lakeside-spinerz"]`, a team a letter
  off its banner, left the Spinners' Bo Nakamura as plain `bo-nakamura`, exited 0,
  and printed 8 lines under `--quiet` with no word of the entry. It prints the
  warning once, in 13. So do an entry no card matches and one whose teams no one
  competition holds two of. An entry that waits on a card counting for nothing, a
  washout or a night not yet played, is still a note. So is one with a team that a
  `teams = [...]` list declares and no card prints yet, which was told to check that
  team against the banner it is printed under, and is told it waits for that team's
  first card. Where the rest were separated, the teams no card prints the name
  under are counted: three were told `that one added nothing, so check it` and are
  told `those added nothing, so check each of those`. An entry that left one record,
  and was told `The rest were still separated`, is told `so the entry separated
  nobody`. One that named a team of each of two grades and parted two players of one
  of them was told it `separated nobody. Leave it off`, and is told nothing now.
- A `[team_names]` key no team has is said on every run, with the key to use.
  `"riverside-rocket" = "The Rockets"`, a letter off, exited 0, printed 8 lines under
  `--quiet`, and left the team `Riverside Rockets`. The run says `[team_names] names
  riverside-rocket, but no card this run read prints a team riverside-rocket, and no
  [competitions] section declares one, so the name reaches nobody. Did you mean
  riverside-rockets? Keyed on riverside-rockets, the line is riverside-rockets = "The
  Rockets".` A key an `[aliases]` line folds is told the team it folds into, because
  a team is looked up after its alias. A key `strip_team_prefix` also files under a
  shorter id is said once. A prefix is taken off a key only where the key spells it
  as the setting does: under `strip_team_prefix = "North End"`, `north-end-rockets =
  "The Rockets"` exited 0, printed 6 lines under `--quiet`, and left the team `North
  END Rockets`. It prints the warning once, in 10, with the key the team is filed
  under, `rockets`, and the line `rockets = "The Rockets"`. A team that a
  `teams = [...]` list declares and that no card prints yet is no mistake. A key for
  another side of a team, as `riverside-rockets-b` is of the Rockets, is never
  offered the Rockets' key.
- A round printed in fullwidth, Arabic-Indic or mathematical digits is titled `Round ５`
  on the page, as `Round 5` is. The build reads such a label as a number and scores the
  round without a warning, and the page asked a test of its own, which took ASCII digits
  alone: measured on the demo with its round 5 cards printing `５`, `٥` or `𝟓`, the front
  page read `Latest round ５ · Wed 4 Mar 2026` and `#/matches` headed the round `５`
  under `Round 1` to `Round 4`. Each round in `season.json` now says, in `numbered`,
  whether the build read its label as a number, and the page titles it by that alone,
  so a label of 4301 digits, which the build calls a name, is no longer titled `Round
  111...` either. A superscript `⁵` is still a name to both, and F11 still warns about
  it. The field makes the document format `4`: a page given a format `3` `season.json`
  says `This page reads season document format 4 and the data says 3` in its data
  health section, and shows none of the season.
- A round and a fixture in `season.json` list their cards by key alone: each round's
  `matches` and each fixture's `match_id` are removed. Both were written beside the
  `keys` the page finds a card by, and read by nothing, and both missed a card that
  printed no id: measured on the demo with the round 2 Brindle Wombats v Brindle
  Bunyips card printing none, round 2's `matches` held 2 ids for its 3 cards and that
  fixture's `match_id` was empty. Read a round's or a fixture's cards from its `keys`,
  and the number a card printed from that card's `id`. That number also stays in
  `validation.duplicates`, which quotes what each card of a clashing fixture printed.
  The demo's `season.json` is 735 bytes shorter with `numbered` in, and its report and
  every other file of its site but `app.js` and `season-data.js` are the same bytes.
- A result verb is read one way, from one table of whole verbs, by the reader's
  cross-checks and by validate, where the reader looked for marks inside the verb. A
  word the table does not hold is one `verb-unknown` warning, a word to learn, and not
  a result guessed from its letters. A whole-match forfeit printing `BOWS TO`, which
  the reader took for a loss because it ends in ` to`, was an F6 error on the verb
  beside `verb-unknown`, and the build exited 1; it exits 0 now, with the warning
  alone. A verb written with a stop for the space reads as the verb it is, so `DEF.BY`
  is a loss, where the reader took it for a win, and on a card whose Match Forfeited
  box is ticked against the home side it restates the box, where it was an F6 error.
  `DREW`, `LEVEL`, `HALVED` and `BEAT` are verbs of the table now, as `WON` was, and
  `WIN`, `D.`, `WON BY` and `FELL BY` are words it does not know. `VS:`, `(VS)` and
  `-V-` between the team names state no result, to the reader as they already did to
  validate, where the reader called each a verb it did not recognise. `DEFEATS`,
  `DEF. BY` and `VS`, the verbs Match Centre prints, read as they always did. On a
  played card `DREW`, `LEVEL` and `HALVED` read as `DRAWS WITH` does and `BEAT` as
  `BEATS`, where each was a `verb-unknown` warning, so a draw verb over a rubber each
  with the other rows left empty is a `verb-no-result` error, and the build exits 1
  where it exited 0. `tools/doctor.py` finds a card's result verb by the same table,
  where it looked for `DEFEATS`, `LOSES TO` and `DRAWS WITH` alone, so it no longer
  says `result verb not found` of a card printing `DEF. BY`, `BEAT` or `DREW`.
- A draw verb on a whole-match forfeit whose set rows are left blank, or give a rubber
  each with the rest empty, is held to the Match Forfeited box, and is one
  `verb-mismatch` error, since a match one side forfeited was not drawn. `DREW`,
  `LEVEL` and `HALVED` there exited 0 with `verb-unknown` alone, the verb check
  agreeing with level rows nobody played, and `DRAWS WITH` was the error with the
  check agreeing beside it. The check now says the verb is held to the box and makes
  the match a draw, and `tools/doctor.py` says `build.py` reports it once, as
  `verb-mismatch`. A draw verb beside rows filled in for the side that turned up is
  still held to those rows, as `DRAWS WITH` was, and is one `verb-mismatch` error.
  That check says a draw verb `says the match was drawn`, on a forfeit and on a
  played card, where it said the verb `means draw won`.
- A player's name a card prints wholly in capitals is shown with a capital on each
  part: `BO NAKAMA` is `Bo Nakama` and `TUI-MAE O'DOWELL` is `Tui-Mae O'Dowell`,
  where they were `BO Nakama` and `Tui-mae O'dowell`. A player's name went through
  the team rule, which keeps a shouted word of three letters or fewer as a club's
  abbreviation and lowers a minor word, so it kept the given names Bo, Kit and Ros
  shouted and made `JO A SMITH` `JO a Smith`. It is `Jo A Smith` now, in the
  document and on the pages, for a rostered player and a fill-in alike, and for a
  rostered player the review list's `[player_names]` line offers that spelling. A
  name printed in mixed case is read as it was, so `TJ Nakama` keeps its `TJ` and
  `Bo Vasse III` its `III`, and a team keeps its abbreviations: `RTC ROCKETS` is
  still `RTC Rockets`. What the capitals cannot settle, a shouted `TJ NAKAMA`,
  which reads `Tj Nakama`, or a `DE`, which reads `De` where it read `de`, is spelt
  under `[player_names]`. Rebuilt, cards that print a player's name wholly in
  capitals show the name differently.
- A shouted word is capitalised at its first letter, and past a one-letter prefix,
  in a team's name and a player's. `HILLTOP (WOMENS)` is `Hilltop (Womens)`, where
  it was `Hilltop (womens)`, `O'BRIEN` is `O'Brien`, where it was `O'brien`, and
  `D'ARCY'S DRIVERS` is `D'Arcy's Drivers`. Each part after a hyphen or a full
  stop has its capital, so `NORTH-WEST ROCKETS` is `North-West Rockets` and
  `MT.ORRIN ACES` is `Mt.Orrin Aces`. A possessive keeps its letters lower, as it
  did: `KING'S ACES` is `King's Aces` and `KINGS' ACES` is `Kings' Aces`.
- An `A` before TEAM, TEAMS, GRADE, RESERVE, RESERVES, SIDE or SQUAD is a team's
  letter, so `HILLTOP A GRADE` beside `HILLTOP B GRADE` is `Hilltop A Grade` beside
  `Hilltop B Grade`, where it was `Hilltop a Grade`. After a minor word it is still
  the article, and after an `&`: `PART OF A TEAM` is `Part of a Team`. A phrase
  with a word from the list takes the capital too: `WHAT A TEAM` is now
  `What A Team`, where it was `What a Team`, and `FIVE A SIDE` is `Five A Side`,
  where it was `Five a Side`. `WORTH A 1000 WORDS` keeps its capital,
  `Worth A 1000 Words`, because its count reads as `HILLTOP A 2`'s number does.
  `[team_names]` spells any of them.
- A command to paste that is wider than the report goes on over the lines under it
  after a ` \`, where a word begins, and the rest hangs four further in. The demo
  archive's report, the build `tools/demo.py --seasons multi` names as its next
  line, had one line of its 423 past 78 columns, the `--discover --force` offer at
  86, and has none. A flag keeps its value on its line unless the two fit on no line
  together. A path is never broken inside, and one too long for any line is given a
  line of its own. On Windows the command is still one line, since neither cmd nor
  PowerShell reads the backslash as going on.
- The `--only` and `--prune` hints end their sentence at a colon and print the
  command on lines of its own, as the `cd` hint always did. The refusal of `--only
  'Season 2'` wrapped its command with the sentence: an `--out` holding a space was
  broken inside its quotes, where a pasted line break is part of the folder's name,
  and one of 99 characters was cut in two. The stale `--only` note ended in a full
  stop against the command's last word, and the prune retry reads `Delete the
  directory yourself, or move that file out of it and prune again:`.
- The `tools/doctor.py` line under the refused cards goes to the project first when
  the build was run from anywhere else, a folder inside the project included, `cd P
  && python3 tools/doctor.py CARD`, with the card named from the project when it is
  inside it and in full when it is not. Pasted where the build was run, it was
  `python3: can't open file '.../tools/doctor.py'`. From the project it is the
  doctor alone, as the README shows it.
- The F11 warning asks what the club typed on a round, as the finals note does. A
  round `SF` typed `finals = true` in a declared draw was still told `Set finals =
  false under [[competitions.wednesday-division-3.rounds]] if this round is really a
  regular one`, and a round labelled `Bonus` typed `finals = false` was still told
  `round label 'Bonus' is neither a number nor a finals label this program
  recognises; it is being scored as a regular round.`, word for word as with nothing
  typed, where the note offers the setting only to a round read off its label. Only
  a round with nothing typed on it is offered the setting, or told its label is not
  one this program knows, now. What was typed is carried on the round, and the
  warning and the note both read it there. A season with nothing typed on its finals
  builds byte for byte as it did.
- The `card-missing` warning counts the draw's fixtures that have a card, where it
  counted every card of the round against them. With round 1 of the demo declared as
  one fixture that has no card, it said `3 of the 1 fixtures the draw lists for that
  round do have one`, with a detail of `3 of 1 declared fixtures have a card`, and
  with one of round 1's three fixtures changed to a pairing that has no card, `no
  scorecard was read for Brindle Quolls v Brindle Kookaburras, which the declared
  draw lists, and 3 of the 3 fixtures the draw lists for that round do have one`. It
  says `2 of the 3` now, and two cards of one fixture are one fixture with a card,
  where they were `2 of the 2`. A round whose every card is of a fixture the draw
  does not list is said to be: `the 3 cards read for that round are all of fixtures
  the draw does not list`, and `0 of 1 declared fixtures have a card`. Discovery's
  note had called that round `in the declared draw with no cards at all`, and says
  what the warning says now, `round 1 has cards for 0 of its 1 declared fixture —
  Brindle Quolls v Brindle Rosellas is missing, and the 3 cards read for that round
  are all of fixtures the draw does not list.` Both ask one function for the count.
- Discovery's note on a card that `prints no round number` and so `sits at the end of
  the draw` is gone. No card could reach it: the reader refuses a card whose round
  label has no letter or digit, typed `""`, `" "`, `-`, `.` or `_` on the demo's
  cards, with `no line above the set table names both a division and a round`,
  before a match is made of it. The tests now hold the reader to that instead. The
  reports and the pages are unchanged.
- A link at a file a build writes is refused before anything is written, and named.
  The build wrote through it: with `site/index.html` a link to another project's
  front page, that page was rewritten and the run exited 0, and with every file of
  a site a link, all 9 files at the other ends were rewritten. It exits 2 now,
  with one line for each link, such as `site/index.html is a link, and the front page
  needs that name for a file of its own; move it aside or build into a different
  --out directory`, and the careers page's link refusal reads the same way. That is
  so wherever the link leads, inside `site/` too: `site/index.html` a link to a
  dashboard's `index.html` had the front page written over it, so a link that only
  saves a second copy of the same bytes is refused as well. A failed run no longer
  writes its sentence through a linked `build-status.js` into the one another
  front page loads, and `--prune` keeps a dashboard left behind that holds a link,
  which it deleted along with the dashboard, and names the link in the build's
  words. `--out`, a season folder and a competition folder may still be links to
  folders, and are built into.
- `--check` says where a build writes a competition that has a second copy. It said
  `a second copy: this competition is written to site/wednesday-division-3 now` of a
  dashboard renamed by hand, when the run had written nothing and that folder did
  not exist. It reads `a second copy: a build writes this competition to
  site/wednesday-division-3` under `--check`, and a build still says `now`.
- A competition's id or a season's folder that holds a word of a withheld player's
  name stops a `--players` build. The demo with a `[competitions.pemberley-cup]`
  section, which its front page lists before any card is filed under it and where
  Pemberley is a player's surname, built with `--players numbered`, exited 0, said `no
  player's full name is in any dashboard this run built`, and published the id in
  `picker-data.js`. Filed under `data/2026-pemberley` in multi-season mode, it
  published the word in four files and two folder names. Each now exits 2 and writes
  no dashboard, and the report names the id by its shape, says the dashboard's folder
  and web address are made from it, and says where to rename it. `pemberley_cup` and
  `pemberley2026` are refused as `pemberley-cup` is, an underscore or a run of digits
  setting a word apart as a hyphen does. An id's or a season key's own words no longer
  count as words the page prints anyway, and nor does a season's name made from its
  folder when the cards print none. A word that a team's or a competition's name
  prints anyway is not refused, and a whole name in an id is one refusal, not one for
  each word. A section without both a day and a division, which the front page lists
  nowhere and no file names, is not read at all. A label on one that named a player
  stopped the build over a file it was never written to.
- A line under a squad that was read as a heading is searched for in pieces too, cut
  at a bracket, a colon or a word with no letter in it. `Kit Vasse (sub)` under a
  squad, beside a team called `Kit Vasse Shield`, was withheld from its note while the
  team's name printed `Kit Vasse` in three files, and the build exited 0, as it did
  for `Kit Vasse - sub` and `Kit Vasse:`. Each now exits 2, and the refusal names the
  card, says a line on it was read as a heading and may be a player's name, and says
  what to rename. A piece the page prints whole anyway, or one word it prints, is not
  searched for, so `Bravos (emergencies)` beside a team called `Bravos Emergencies`
  still builds, and so does `Division C:` on a card of Tuesday Division C, the
  division as the program names it. `Example Bravos (emergencies)` beside a team
  called `Example Bravos Mixed` is refused where it built, since nothing in a line
  tells a team's name from a player's, and so is `Mixed Doubles:` beside a competition
  labelled `Tuesday Mixed Doubles`. Renaming the team or the label lets it through.
  Where a word of such a line is in a competition's id, the refusal names the card as
  well, and renaming the id's section lets it through.
- A team's rate of sets won is written into `season.json`, as `win_pct` on each team,
  by the rule each player's is, and the team page's Sets tile prints it to one place,
  as every other rate on the page is printed. The tile worked the rate out itself, to
  no place: on the demo, the Brindle Quolls at 10–9 read `53% of sets won`, where the
  players' rule gives 52.6, and they now read `52.6% of sets won`. The other five teams
  read the figure they read, with its place, `65.0%` where it was `65%`. A team that
  has decided no set has a `win_pct` of `null`, as a player who has decided none has.
  The first build after upgrading rewrites each competition's `app.js`, `season.json`
  and `season-data.js`, and the one-file page where one is built, and the next build
  writes nothing.
- A player whose team won no games, and a name on no team's roster, have no share of
  the team's games, where they had one of 0%. On one card on which the Example Alphas
  win every rubber 6–0 and a name on neither roster plays their second singles,
  `season.json` gave the three Example Bravos and the stranger a `share_pct` of `0.0`
  of `0` team games, the `0.0` it gave Charlie Cotterill, who is on the Alphas' roster
  and won none of their 24. Both kinds of player page read `Team share 0.0%` over `0
  of 0 team games won`. Only Charlie's is a record. `season.json` now gives the other
  four `null`, and a team that won no games a `null` in `contribution_gap.share_pct`
  as well. The Share column on the team page and the Players tab shows a dash, Copy
  and CSV leave the cell empty, and the sort puts them below every real 0%. The player
  page's Team share tile shows a dash, and under it a name on no roster reads `on no
  team’s roster`. A name on no roster is not credited to the side it played for: a
  card says which side they played for, not whose roster they are on. Both of these
  changes are part of format `4` as well: it adds each team's `win_pct`, and gives
  `share_pct` as `null` where format `3` gave `0.0`, so a script of your own that
  reads format `4` has those two to allow for too.
- A result verb the set rows contradict is one error, `verb-mismatch`, and a Match
  Winner tick naming the side the verb names goes with it. A played card with rows
  6-2, 6-3, 6-4, 6-1, `LOSES TO` and the away tick was three errors, F6 on its verb,
  F6 on its tick and `verb-mismatch`, and so was the same card with the away side's
  Match Forfeited box ticked. With no tick, or one the rows agree with, it was two.
  Each is `verb-mismatch` alone now, which says what the verb means and what the
  card is scored on. A tick for a side the verb does not name, such as the away tick
  beside `DRAWS WITH` over a home win, is a second thing the card says, and keeps
  its own F6. Where `verb-mismatch` does not report the card, because it was read
  0-0 on every row and counts for nothing, finished too few rubbers for a result, or
  is a whole-match forfeit whose verb restates its box over rows filled in that do
  not bear the verb out and that `forfeit-contradicted` passes by, F6 still reports
  the verb, and on the first two a tick restating it too.
- A TOTAL row the set rows do not add to is one error, `totals-mismatch`, which
  names the cells, where F6's totals cross-check reported it too. A whole-match
  forfeit whose rows give no result may now total what its box awards: every rubber
  of the format to the side that turned up, each a love set under the club's set
  rule, 4-0 and 24-0 over four rubbers of `first-to-6` or `six-game-tiebreak` and
  4-0 and 16-0 of `fast4`, or the sets or the games of that alone. Each was two
  errors and builds with exit 0 now, as a TOTAL row the rows add to did. Any other
  TOTAL row on such a card is one error, `the printed TOTAL row and the away Match
  Forfeited box disagree on home games`, with the award and what the rows add to.
  `tools/doctor.py`, which knows no club's set rule, says the totals cross-check of
  such a row that the rows do not add to agrees if it totals the award under some
  set rule, which it names, and disagrees otherwise. The award's sets are what
  `rubbers` pays a forfeit. A washout's TOTAL row is still held to nothing.
- A banner beside a side of a club is asked about that side when its tag is not in
  the club's own scheme. With Hilltop A, Kenmore 3, the Spinners and the Wanderers
  declared, a card bannered HILLTOP AA, HILLTOP 1 or KENMORE E stopped the run with
  an F8 that asked nothing, and the page asked nothing either, because any trailing
  tag of one or two characters was taken for another side and any digit for a side's
  number. F8 asks `Did you mean hilltop-a?` or `Did you mean kenmore-3?` now, and
  the page the same. A tag is a side's when it has the shape of the side's own, a
  letter beside a letter, two beside two, a number beside a number of any length,
  since numbered sides run past nine, or when the other side prints no tag, so
  HILLTOP B beside Hilltop A, KENMORE 33 beside Kenmore 3 and HILLTOP beside both
  Hilltop sides are still asked nothing. Of 3,993 banners, HILLTOP and every
  one-edit slip of Hilltop A, Hilltop B, the Spinners and the Wanderers, 705 were
  asked nothing, 580 of them with a digit in, such as `hill1top-a` and `hilltop-1`;
  25 are now: HILLTOP, and a side lettered C to Z. A `[team_names]` key `hilltop-1`
  or `hilltop-aa` that reaches nobody is offered its line keyed on `hilltop-a` the
  same way, where it was told only to check the key against the banners.
- With no team list declared, a banner that looks like another team's is paired
  with the team nearest it alone, the team F8 would ask about under a declared
  list. With the Rockets and the Aces printed HILLTOP A and HILLTOP B and a card
  bannered HILTOP A, discovery wrote two notes, one pairing it with HILLTOP A and
  one with HILLTOP B, two edits off; it writes the first alone now. Of the 2,903
  one-edit slips of those four teams and `hilltop`, 840 were paired with more teams
  than F8 would ask about, and each is paired now with the teams F8 asks about. Two
  sides of a club are still paired with nothing.
- A banner a declared team list lacks is reported once, by F8, with the line that
  fixes it. Discovery wrote a note about the same banner beside F8's error, so the
  report asked `Did you mean` twice, and only the note, which `--quiet` leaves out
  and season.json never holds, had a remedy: `An [aliases] entry hiltop-a = <the
  right slug> is the one-line version.` The note is gone, and F8 ends `Either the
  banner or the list is wrong; if it is the banner, hiltop-a = "hilltop-a" under
  [aliases] is the one-line fix.`, a line that, pasted under `[aliases]`, builds
  clean. An `[aliases]` line applies to every competition, so none is offered for a
  banner that is also a team of another competition on the club's cards: with
  Kenmure 3 declared in Division B and a Kenmore 4 of Division C's, a B card bannered
  KENMORE 4 is told `No [aliases] line is offered: kenmore-4 is also a team of
  thursday-division-c`, and to correct the card in Match Centre, where
  `kenmore-4 = "kenmure-3"` would have filed C's Kenmore 4 under kenmure-3 with a
  clean build. Nor is one offered for a banner near no one team, or as near two:
  `the cards do not say which that is, so no line is offered.` The note also said
  the team's matches sat outside the ladder, where the Spinners, who played the
  card, were credited with it. The page's warning on the match is unchanged.
- A night whose cards disagree registers only the names they all print in a roster
  block. Both cards count until the rule for repeats sets them aside, so each
  registered its whole roster, and a name only one of them printed became a player of
  the team. With a second round-four Quolls v Magpies card added to the demo, its
  first rubber's games swapped and `Yan Stray` added to its Magpies roster, the
  report gave the F9 error and 10 warnings where the demo has 6. The four more were
  `roster-incomplete` on the Magpies' cards of rounds one to four, the first
  round-four card among them, each saying its roster `omits registered player(s)
  yan-stray`, and `yan-stray` was a Magpie with no matches and no rating. It gives the
  error and the demo's 6 now, and he is nobody's player. A card of such a night that
  prints no roster for a side takes nobody off that side, and a washout of the night
  is one of its cards. A `[player_names]` or `[namesakes]` line under a name only
  some of the night's cards print is told it waits for a card that counts, as a line
  under a washout's name is. So is a captain declared under such a name, and one
  declared under a name only a washout prints, who was told to `Check the spelling
  against the roster printed on a scorecard` of a name spelt as the card prints it;
  that is said now only of a captain no roster prints. This covers only a night whose
  cards disagree: a copy of a card, and a card whose result another card agrees with,
  are unchanged. The demo, and a night whose cards disagree about the result and not
  about the rosters, build byte for byte as they did.
- The `roster-mismatch` warning is gone. It held each card's roster against the
  squad, and the squad is made of the rosters of the cards that count, so no build
  reached it. With a night's disagreeing cards registering only the names they all
  print, one card would: with the check left in, the card printing the stranger above
  is told `the away roster names yan-stray, who are not in brindle-magpies's
  registered squad.`, which is the night F9 already reports as an error, told again
  as a warning about one of its cards. `roster-incomplete`, what a card's roster
  leaves out of the squad, stays, and the test that held the order of the squad the
  removed warning listed goes with it.
- A side that forfeited the whole match and played no rubber of it is not asked for
  its roster. The card of a night the away side never came to, Match Forfeited ticked
  against them, every row 0-0 and nothing printed in their roster block, was told `no
  away roster was read from the card, so fill-ins on that side cannot be detected and
  nobody who played away has a rating for this round`, of a side nobody played for: 2
  warnings, where it now has only the F8 of a season of one card. One that read
  neither roster said `neither roster was read` though the home side came, and now
  asks for the home roster alone. The side that came still owes its roster, as does a
  side that gave the match up after playing a rubber, and every played card. The rule
  is the one `set-no-games` and `set-players-missing` already pass a forfeit's empty
  rows by with.
- The `config.toml` a run writes has `scoring` and `sets` behind a `#`, `# scoring =
  "games-bonus"` and `# sets = "first-to-6"`, under a note to remove the `#` once
  each is the competition's, and live only where the club declared them, the
  default's own name included. Both were written live, as if the club had chosen
  them, and the note `sets are being scored as first to 6 (first-to-6) and the
  ladder as ... check them under [rules] in config.toml` was printed only while
  there was no file: once on the demo's first run, on none after it, and on none
  over a file holding only `[club] name`. It is printed for each competition whose
  points scheme or set rule neither its own section nor `[rules]` declares, until
  the club has declared both, and ends by naming the one left: `This is asked on
  every run until sets is declared there, with no # in front.` Where every
  competition sets both in its own section, `[rules]` says so and offers neither;
  a rewrite wrote the defaults there live, a line that reached no competition. A
  rule left off is `""`, as it already was in a competition's section, and the
  default is applied where a rule is asked for. The demo's pages are byte for byte
  as they were. The README's `--only` example counts 14 notes where it counted 12,
  the note on each of the two competitions it leaves out.
- A `config.toml` written before any card was scored offers no match shape under
  `[rules]`, and says in words what `format`, `slots` and `min_sets_for_result`
  take. It offered the four-set default as if a card had given it, `# format =
  "doubles-singles-doubles"`, its slots and `# min_sets_for_result = 3`. With the
  `#` of the format or the slots line deleted, the demo's doubles-2 cards then ended
  `exit 1: 15 card(s) found and not scored, nothing built`, and with that of the
  number the run stopped at exit 2, `[rules] min_sets_for_result = 3, but the cards
  of wednesday-division-3 have 2 set row(s)`. A file whose every competition sets
  its own shape or number says that under `[rules]`, where it offered the same
  default.
- The byes table a run writes says in words how a line is typed, the team's name in
  double quotes, then `=` and the rounds in square brackets, and points to the one
  worked through in `config.example.toml`. Its example line was `#   "Brindle
  Bunyips" = [1, 6]`, the club's own first team resting two rounds nobody said it
  rested, and with its `#` deleted the demo's next run ended `exit 1: 5 validation
  error(s)`, since a byes table's keys close the team list. Over a declared team
  list, whose rewrite writes the cards' own byes behind a `#` below it, the file
  then would not load, the example's key repeated. With every `#` of the table
  deleted, the demo's ladder and its rounds' byes are as they were.
- The review says a value is split when its lines are commented out over two or
  more blocks of one table, each under its own `# [x]`. With the demo's six team
  names moved under a `# [team_names]` at line 75 and another at line 168, three
  under each, the next run said `not in it; add the lines below to adopt it` of a
  file that held all six, and deleting the `#` of both blocks stopped the run after
  at exit 2, `Cannot declare ('team_names',) twice`. It says `commented out, but
  split between the # [team_names] at lines 75 and 168, and a file declares a
  table once: move its lines under one of those headers, and delete the # to adopt
  it` now, and doing that builds all 15 cards. Where another value listed opens
  the same table, as a second competition's team names do, the lines go `under
  the same # [team_names] as any other value here you adopt, or under one of
  those headers if there is none`. With that competition's two names under the
  second header, gathering under the first stopped the next run at exit 2 again.
  Doing what it says builds all 17 cards, with the two under the second header or
  under a third that holds none of the six. Only a plain table is one table over
  its blocks: two `[[x]]` blocks are two entries, and one entry's values over the
  two are still not in the file.
- The `--discover` refusal names each sheet line whose figure `--force` would drop.
  In a table it can otherwise compare, a line whose points are not a number, whose
  matches played are not a whole number, or which is no `{ points = …, played = … }`
  at all was written back without what was refused, after a refusal that said
  everything declared is written back, and the run after it said nothing of the
  line. The refusal now says `except what no build could compare in the 'Brindle
  Magpies' line of the [competitions.wednesday-division-3.expected] table: put it
  right first`, naming several lines together by table, and beside the draw or a
  table it can compare no line of, names them again after those. A table it can
  compare no line of is still named whole, and not line by line as well.
- `--discover --force` writes the season length the run after it will count rounds
  by. The draw goes, and every `finals` typed in it goes too, so after it a round is
  a final only if its label names one; but the length written was counted under the
  typed flags. With round 3 typed `finals = true` it wrote `rounds_total = 2` and the
  next run exited 1 on F3, three regular rounds played; with `SF` typed
  `finals = false` it wrote 4, and the next run read `3 of 4` with the semi-final a
  final. It writes 3 in both now, and the refusal says so beside the round, for each
  competition whose rounds move, whether its length moves or not: `rounds_total = 3
  is written for <id> where your draw gave it 2 rounds, unless its cards have played
  past either length`. It is printed before a card is read, so it cannot yet say
  which. Counted so, a length that the cards of a round the draw does not list have
  played past is written commented out, with a comment that says why, as one this
  run finds played past already was, and the rounds played the comment offers
  instead are the run after's count too: it offered `# rounds_total = 2` beside "2
  rounds played" where the run after played 3. That costs one draw a live length it
  had: for rounds 1, 2 and `SF` typed `finals = false`, with round 3 carded and not
  in the draw, `--force` wrote `rounds_total = 3`, counting the semi-final, and the
  run after read `3 of 3` only because round 3 stood in for it. That length is now
  written commented out, with its reason, and the run after reads 3 rounds played of
  a season of no stated length. A draw whose every regular round was typed a final
  is written the length its labels give, where a second `--force` wrote a different
  commented line from the first. A `rounds_total` you typed is written as typed.
- Every file of the site is written whole or not at all, to a hidden
  `.<name>.tmp` beside it and then renamed into place, and a dashboard writes its
  data first and its `season.json` last. On the demo with a card taken away and
  the build held to 94685 bytes a file, the way a full disk stops a write, the
  run exited 2 naming no file, with `season.json` cut off at 94685 of its
  151154 bytes; it listed the change as made on a page whose data was last
  week's, and the run after, reading no document, listed nothing. With a folder
  at `season-data.js` the change was said as made the same way and never again.
  Each now exits 2 naming the file, `site/wednesday-division-3/season-data.js`,
  with every file of the dashboard as the last build left it and no `.tmp` file
  left, lists the change under `changes a build would make`, and the run after
  lists it as made. A first build held one byte short of `season-data.js` left
  no file under `site/`, where it left `app.js` cut off at 98780 of its 229098
  bytes and named no file. The disk needs room for a file's new copy beside the
  old one, and a write that stops at `season.json` has written the rest of its
  dashboard: the page shows the change the report lists under `changes a build
  would make`, and the run after lists it as made. A file keeps the mode it had,
  `0600` included, and one made read-only is replaced and stays read-only, where
  a read-only `season.json` stopped the build at exit 2. A second name for a
  file, a hard link to the front page from outside `site/`, keeps what it held,
  where the build rewrote it at exit 0. A dashboard folder made read-only stops
  the build at exit 2, `Permission denied` at its `season-data.js`, with its files
  as they were: it exited 0, rewriting its changed files in place. A read-only
  `site/` stops it at exit 2 at `site/index.html`, after each dashboard under it
  is written whole and its changes said as made, where it exited 0 having written
  4 of 9 files; the front page cannot say that build failed, because
  `build-status.js` is in `site/` too. A folder at a `.tmp` name stops the build
  at exit 2 naming that folder, with it and every file left as they were, where
  the build passed over it at exit 0, and a link at one is removed, never written
  through. A shell the one-file page cannot be folded from stops the run before
  the first file of the dashboard is written, where five were. A rebuild with
  nothing new writes nothing, and the demo's pages are byte for byte as they
  were.
- A scheme whose points are the sets won says what it pays a match forfeited whole.
  Under `rubbers` the filed card, Match Forfeited ticked against the away side and
  every row 0-0, paid the side that turned up 4.0 and the side that stayed away 0.0,
  while the report's scoring row, the note asking a club to check its rules,
  season.json's `scoring_detail` and so the page's rules banner and its `Points
  earned by round` subtitle said `points = sets won; decided on sets then games`, by
  which the side that turned up had won nothing. They say `points = sets won; a
  match forfeited whole pays the side that turned up every rubber; decided on sets
  then games` now, as do the option table in the config.toml a first run writes and
  the README's table of presets. A ruleset built in code whose `forfeit_award` is
  `fixed` names the points it pays instead. Every rubbers site's bytes change once
  for this.
- A shape declared by its `slots` is named as the same shape declared by `format`
  is. Under `[rules] slots = ["doubles", "singles", "singles", "doubles"]` the
  report's format row, a competition's `rules` line, season.json's `format` and the
  page's rules banner said `d1-s1-s2-d2`, where `format =
  "doubles-singles-doubles"`, and cards read with neither, said
  `doubles-singles-doubles`. The two declarations build the same site now, byte for
  byte, and a shape with no name is still named by its pattern, `d1-d2-s1`. Only the
  name changed: a card is checked against the slots as it was.
- `[build] seasons` refused says the line and the two values it takes, in words:
  `[build] seasons = "singel" is not "single" or "multi". Did you mean single?`,
  where it said `[build] seasons must be one of ('single', 'multi'), got 'singel'`,
  a Python tuple, and asked nothing. The players line lists its three the same way,
  `is not "names", "initials" or "numbered"`, where it said `is not one of` them.
  `unplayed`, under `[rules]` or a competition's own section, says `[rules]
  unplayed = "averge" is not "average" or "zero". Did you mean average?`, where it
  said `must be 'average' or 'zero', got 'averge'` and asked nothing. A ruleset
  built in code with a `forfeit_award` it has no rule for is told `forfeit_award =
  "fixd" is not "as-played", "full-games" or "fixed". Did you mean fixed?`. One
  function words all four lists.
- The club name offered for `config.toml` is chosen by the rule the `foreign-card` check
  takes a competition's letterhead for the club's by, one function asked of every
  competition's cards. It was the spelling printed most, so the demo with nine of its
  cards printing its name in capitals, title case and lower case, three each, and six
  printing another club's, was offered the other club's name: in the report's note,
  `season.json`'s `meta.club`, the front page's `<title>` and the `# name =` line of the
  `config.toml` it wrote, while the check named those six as possibly another club's. A
  tie of spellings went by code point, so seven cards in capitals read first and seven
  in title case offered the title case where the check quoted the capitals. Cards are
  now counted by letterhead, as the check counts them, over every competition in the
  order their paths and pages are read, and the name is the spelling the most of that
  letterhead's cards print, however late the first of them is read. A tie of
  letterheads, and of spellings, goes to the one read first, in the name and in the
  check, even where one of the two letterheads is printed several ways: another club's
  seven cards read first, four in capitals and three in title case, against the demo's
  seven in capitals, now give the other club's name, and the check names the demo's
  seven, where before both took the demo's for the club's. The nine-and-six demo is
  offered `BRINDLE CREEK TENNIS CLUB INC` in all four places, and the demo itself builds
  byte for byte as it did. In a season of several competitions, one most of whose cards
  print another club's letterhead is still checked against that one.
- A `foreign-card` warning about several cards lists them as `a, b and c` and their
  spellings as `'A', 'B' or 'C'`, and speaks of them as several: "these cards'
  letterheads read", "They have been scored and their results are in the ladder",
  "take them out of data/". It said "this card's letterhead", "It has been scored" and
  "take it out" of however many cards it named, with the cards joined by commas alone
  and the spellings by `or` between each two. A warning about one card reads as it did.
- F9's warning about cards that say the same thing names the file not counted:
  `Delete the spare file data/demo-season/round-05/bunyips-v-kookaburras.pdf, the
  one not counted, to keep the folder honest.`, and of three cards both copies, as
  `the spare files ... and ..., the ones not counted`. It said `Delete the spare
  file from data/` beside a list of both files, either of which can be the one the
  season leaves out. The file named is the one `stats.duplicates` sets aside, the
  rule that chose the card counted. On the matches page a copy's opened card has a
  `Copy` note saying the night is counted once, naming the other cards, and
  that no figure moves when this file leaves the data folder: it said `copy of
  another card — counts for nothing` in its summary and nothing in its body. The
  note on both cards of a night named two ways names the other card too, in the
  sentence the note on a night the cards disagree about already named them in.
- A run in single-season mode says once, among its notes, when every fixture more
  than one card claims has cards in more than one folder directly under the data
  folder: `Every fixture more than one card claims — 15 of them — has cards in more
  than one folder directly under data (2025-winter and 2026-winter), as seasons
  filed side by side would.`, then that `seasons = "multi"` under `[build]` makes
  each folder a season with no card deleted, and that if they are not seasons F9
  stands. The demo written twice into those two folders gives every fixture one
  date, so no round held two years, and the run was told 15 times to delete a
  spare file and never about the setting; the demo's own two seasons of 2026,
  built without their config, had 14 F9 errors and no word of it either. It is not
  said beside the note about years, which asks for the same line, nor when a
  repeat is inside one folder, by a card directly under the data folder, in
  folders one level down, or in two folders whose names give one season, where
  the setting separates nothing. F9 does not read the folders. A card filed a
  second time in another round's folder is told it too, and the README says it
  can be ignored there.
- No note or review of a build of the demo's two seasons asks for a line of
  `demo-seasons.toml`, which the demo writes again every time it runs, so a line
  typed into it was gone after the next one. Its report named the file on 25 of its
  438 lines: 14 of its 16 notes asked for a line there or under a table of it, and a
  review of 12 values ended by offering to rewrite it with `--discover --force`. It
  is 135 lines now, with none of them. The two notes left say which folder filed each
  season, each season's block still says its 6 teams are derived and that with no
  season length declared no percentage is shown, and the rules in force are still
  printed above them. What the notes that went began with is not in the report any
  more: the club name the letterhead prints, the team names un-shouted and the
  captains read off the (C) marks are on the pages it builds, and that a derived team
  list credits no bye is not said. The build knows the file by its name and the
  comment the demo begins it with, so a club's own file of that name and those
  settings, or the demo's copied to `config.toml`, is told all of it as before. The
  site the demo's build writes is the same byte for byte. On an odd draw the
  demo's own note on byes says `demo-seasons.toml` declares no team list, where it
  said only a list declared there turns a bye into a scored one.
- The README's plain demo said that once its cards were deleted the next run listed
  the dashboard they built under `left behind`, for `--prune` to clear. With no cards
  of the club's own in `data/` yet, that run ends `exit 1: nothing was built` and
  lists nothing, `--prune` ends the same way, and `site/wednesday-division-3` stays.
  It says now that the first build of the club's own cards lists it, which the next
  `--prune` clears, and that with none yet `site/` is deleted by hand, as the
  two-season paragraph already said. And where there was no `config.toml`, the demo's
  build wrote one from its cards, whose section for the demo's division kept it on
  the front page after all of that, greyed, `No results yet`: the README says to
  delete that file too, and the next build writes a fresh one.
- A card with `Match Cancelled` ticked that names a winner, by a `Match Winner` tick
  or by a verb such as `DEFEATS`, now fails the run. A night called off has no
  winner, so the card contradicts itself, and either the ladder is missing a result
  or the card names one nobody won. Of 17 washouts built beside the test season, the
  11 that named a winner, over rows left blank, over two rubbers or over four, built
  with exit 0 and the warning that the match was recorded as cancelled their one
  finding. That finding is now an error saying what the card claims: `recorded as
  cancelled, so it is not counted in the ladder, but the card ticks home as Match
  Winner and the verb 'DEFEATS' means home won`. It is still the one finding about
  the card, and its rows are still held to nothing. The 6 that name no winner,
  printing `DRAWS WITH`, `VS`, no verb or a word this program does not know, with no
  tick, build as they did. `tools/doctor.py` says so of such a card too: its verdict
  had gone on saying the build reports the match once, as cancelled, and now names
  what the card claims and says that report is an error, which fails the run.
- A card and the match made from it are named by one function. The validator had a
  copy of its own, which named a card with no file name differently from its match;
  no card read from a file was named differently.
- The review item asking how many sets a result needs counts every `verb-no-result`
  the report lists for the competitions it asks about when it says how many cards
  print a result anyway, which is the list it points at. It kept to the short
  matches' own cards, which on every card read from a file is the same list.
- A comment, a fill-in's line or a player's name holding the word `Fill-ins`,
  `Reserves` or `Substitutes` no longer ends the squads where it is printed. The
  roster block ended at the first such word under `Players`, whatever line it was on,
  as it ends at a fill-ins heading. Measured on cards from the card writer, with
  `Emergencies` printed over both squads and no `Comments` heading: a comment reading
  `Fill-ins arrived late`, `Reserves arrived late` or `Substitutes arrived late` read
  4 and 4 with no note, `Emergencies` a player in both squads, and on both legs of
  one fixture 7 players and 2 warnings, `player-two-rosters` among them, where there
  are 6 and 1. A fill-in printed as `Substitute Golf Garside`, `Reserve Golf Garside`
  or `Fill-in Golf Garside` under `Emergencies` read 4 and 4, and on both legs 8
  players and 6 warnings where there are 7 and 3. A rated player printed third as
  `Reserve Quill`, `Substitute Quill` or `Fill-in Quill` read 2 and 2, the other
  squad's third player lost with him. The word now ends the block only on a heading's
  line: the words printed across the card on its baseline, each of them another such
  word, a side word (`Home`, `Team`, `Visitors`, `Players`), one with a bracket in it
  or with no letter or digit, or anything before the word in brackets, `Bravos
  (Substitutes)`. A rating is digits, so a rated line is no heading, and `Home Team
  Fill-ins` and `Visitors Reserves` are headings. Each of those cards reads 3 and 3,
  the comment's and the fill-in's with the note that `Emergencies` was read as a
  heading, and on both legs the comment gives 6 players and 1 warning, the fill-in
  7 and 3, and the Quill 7 players where there were 6. Any other comment holding the
  word
  reads as the same comment with another word in its place, which is not always right
  (filed): `Substitutes: 2` as `Friends: 2`, `Emergencies` still in both squads but
  now with the note quoting the comment, and `Two reserves: Golf Garside` as `Two
  friends: Golf Garside`, both squads lost to the note that nothing says where the
  block ends, on both legs 4 players and 7 warnings where there were 7 and 2.
  A comment that reads like a heading, `Golf Garside (substitute)`, `Reserves (none)`,
  `Reserves: -` or `Home reserves`, still ends the block, as it did (filed). The cost:
  a heading naming a team before the word outside brackets, `Alphas Reserves` over
  one squad and `Bravos Reserves` over the other, or the word beside a heading in
  another word on one row, `Fill-ins` or `Reserves` over one squad and `Emergencies`
  over the other, ended the block by the word alone, and now reads as a heading in
  another word does, as a player in its squad with no note: on both legs, with a
  fill-in, 9 players and 8 warnings where there were 7 and 3, and with none 8 and 7
  where there were 6 and 1 (filed). Troubleshooting entry: *The report
  says a card's roster was not read*.
- `tools/doctor.py` finds a fill-ins heading worded `Reserves` or `Substitutes`, where
  it said `fill-ins not found` though the reader ended the squads at it. It looked for
  `Fill-in` with a pattern of its own, which the reader's rule had outgrown. Measured
  on cards from the card writer: `Reserves` printed over both squads, over the home
  squad only or over the away squad only, `Substitutes` over both, `Reserves:`,
  `Reserve Players` and `Bravos (Substitutes)` each gave `fill-ins not found` beside a
  card read 3 and 3 with no note; and the pattern found `Fill-ins` in a comment and
  `Fill-in` on a fill-in's own line, where the reader no longer ends the squads. The
  field is now asked of the reader: the heading it ends the roster block at, under
  `Players` below the set table, or from the top of a page whose set table it
  refuses, said as printed but for a colon or a bracket round it: `found 'Reserves'`,
  `found 'Substitutes'`, `found 'Reserve'`. A comment or a fill-in's line holding the
  word is `not found`, and so is a team's name holding it in the result line, `EXAMPLE
  BRAVOS (RESERVES)`. A team list typed out, with no `Players` heading, is `not found`
  too, and is told the page prints none of a card's words, as one with no fill-ins
  line is, where one printing `Fill-ins` was told it prints a card's words. Under
  `--redact` a heading worded `Reserves` or `Substitutes` is shaped, `found '<8
  chars>'`, as a word the template does not print is (filed).
- A player every card spells the way an `[aliases]` line calls wrong is now said on
  every run, `--quiet` included, with the key to write the name under. An alias says
  which player a spelling is, not how to spell them, and where no card gives a
  spelling it does not fold, the dashboard kept the folded one. `"tui-mae-odowel" =
  "tui-mae-o-dowell"`, for a fill-in printed only as `Tui-Mae ODowel`, showed
  `Tui-Mae ODowel`, and a `--quiet` rebuild printed the 7 lines a run with no
  `config.toml` prints. The warning names `tui-mae-o-dowell` as the `[player_names]`
  key and leaves the name to the club, which no card there spells right; for a
  player `[namesakes]` split off, the key is the name and the team. Each page is
  judged, the careers page too, and a player is said once. A player with a
  `[player_names]` line is not said, nor one another warning already hands a line
  to, so one fix is asked for once. Troubleshooting entry: *A player is shown by the
  spelling an alias calls wrong*.
- A `[player_names]` line under the wrong key that would give its player the name
  they are shown by already is told to delete the line, where it was offered the
  line keyed right. `"bo-nakamora" = "Bo Nakamura"` beside the alias folding that
  spelling, and `"bo-nakamuru" = "Bo Nakamura"` with no alias, were each offered
  `bo-nakamura = "Bo Nakamura"`, a line that gives no page a new name. Names are
  compared as a page shows them, spaces run together and case kept, on every page
  that shows the player, so `"BO NAKAMURA"`, or a name one grade shows and another
  does not, is still offered its line. So is a registered player's name this
  program worked out, such as one un-shouted from cards that print it in capitals,
  since the review block asks for that very line to settle it. A fill-in is in no
  review, and the line keyed right leaves the report and the site byte for byte as
  they were, so it is told to go.
- A misspelling of a name `[namesakes]` splits, given that name under
  `[player_names]`, is now offered the `[aliases]` line that joins it.
  `"bo-nakamora" = "Bo Nakamura"`, for the Spinners' Bo Nakamura split from the
  Rockets' and misspelt in one round's set rows, was told no line is offered, when
  `"bo-nakamora" = "bo-nakamura"` under `[aliases]` makes one Spinners record: the
  split is applied after the alias, by each side's team. It is offered only where
  every card printing the misspelling prints it for a team the entry lists, a roster
  block or a washout's set rows among them, and the split made each such team a
  player of the name. An alias applies to every card, so where a team the entry
  leaves out prints it, that team would get the bare name; and where the split made
  a team none, as when its every card prints the misspelling, the alias would make
  that team a record of its own, still apart. Either way the team is named and no
  line is offered.
- A result verb a card prints but nothing can use is said as printed. Printing `VS`
  between the team names, or a word no table holds such as `TROUNCES`, describe and
  `tools/doctor.py` called the card's verb check `not printed`, whatever else the card
  printed. On a card with no TOTAL row and no Match Winner tick, F6 warned that it
  `prints no TOTAL row, result verb or winner tick`, and the reader that it prints `no
  result verb`, beside `verb-absent` or `verb-unknown` quoting the word. Describe and
  the doctor now say `printed, not usable` of such a verb on any card, and on a card
  no cross-check confirms, the doctor's paragraph under its table says `Nothing this
  card prints can serve as a cross-check`. F6 says `the card prints no TOTAL row or
  winner tick, and it prints 'VS' between the team names instead of a result`, or `and
  its result verb 'TROUNCES' is not one this program recognises`, and the reader says
  the same. Such a verb still confirms nothing, so the warnings stand and the exit is
  unchanged. A card printing no verb is told what it always was.
- On a whole-match forfeit whose rows give no result, F6 and the doctor say a
  cross-check disagrees with the Match Forfeited box it was held to, not with the set
  rows. With the box ticked against away, four 0-0 rows and the away Match Winner
  tick, the build's one error was `the winner-tick cross-check disagrees with the set
  rows`, over a detail naming the box, and the doctor's paragraph under its table said
  the same. Both now say `disagrees with the away Match Forfeited box`. A TOTAL row on
  such a card that totals neither the rows nor the box's award is held to the box
  too, as `totals-mismatch` already said. The exit is unchanged, and a played card's
  report is word for word what it was.
- A round the declared draw types `finals = true` under a name of its own, such as
  `Major Semi`, is played where the draw puts it: after every final the draw lists
  above it, in the week of the latest. It had sorted with the named rounds, ahead
  of every final, so the quarter-final's card said the season was past it and asked
  for a card of a round nobody had played. The season document's rounds, fixtures
  and matches, the notes, the checks, the list of what changed since the last build,
  a player's rating history and the front page all take that order. A round
  numbered, or named as a stage of the finals, keeps its place whatever is typed on
  it, and a season with no declared draw is ordered as it was.
- The tests refuse a width or a floor in a length on the paragraphs behind the rules
  banner's *What these mean*, on what holds them from the page's root down, and on
  any box the stylesheet makes inside one of those, on every screen the stylesheet
  names. They refuse a style set on any of them by the script or the page too,
  which wins over the stylesheet. The paragraphs' 60ch cap only narrows them. A
  `min-width: 60ch` or a `width: 78ch` beside it made the paragraphs 429px wide in a
  351px banner and the page 441px wide on a 375px phone, which then scrolled
  sideways, and every test of the page passed. So did the same floor or width by its
  logical name, a width of 150% or `max-content`, a floor for phones or for dark
  mode alone, the same floor on the disclosure or on the box it keeps the paragraphs
  in, or set on them by the script, and a width on the banner, which made the page
  558px wide at 375. A floor under the cap passed too, and it is wider than a phone
  at some size of type: `min-width: 40ch` made the page 378px wide at 375 under a
  16px minimum font size. Only `auto`, `none`, a zero or a percentage of at most
  100% is let through. The summary, the banner's own lines and a paragraph's bold
  label are not read yet, and nor is the disclosure's flex basis. The page is
  unchanged.
- The tests read a rule inside `@media not print` as the screen's, which it is. They
  had read it as the print block's, so a floor inside one passed and made the page
  441px wide at 375, and the summary hidden inside one instead of in print took the
  tap away on every screen and passed as well.
- The lines behind *What these mean* are not kept to 80 characters in every face,
  and the comment on their cap now says where. In Ubuntu and Ubuntu Sans, the faces of
  Ubuntu's own desktop, a format of four singles, or of three singles and a doubles in
  any order, under either tiebreak rule is a line of 82 at 60ch, and one of eleven
  singles under four of the set rules, or of twelve first to 10, a line of 81; 58ch
  keeps them to 80. Segoe UI, San Francisco and Roboto, which system-ui is on
  Windows, Apple's systems and Android, have not been measured at all. The cap stays
  60ch until they are.
- Both GitHub workflows check out with `actions/checkout@v5` and set up Python with
  `actions/setup-python@v6`. They ran `@v4` and `@v5`, which the first run of the
  tests workflow on GitHub warned are Node.js 20 actions and deprecated. By the
  actions' release notes the new versions are the first of each on Node 24. The
  change was made without a run on GitHub, so the next run is the first to show
  them. Every demo, build and `--check` command the two workflows run was run on
  Python 3.12 before the change and after it, and exits, prints and writes as it
  did, but for the time each build stamps. The demo workflow's
  `upload-pages-artifact@v3` and `deploy-pages@v4` are as they were: the warning did
  not name them, and it came from the tests workflow, which runs neither, so whether
  they are warned about too is for a run of the demo workflow to say.
- A refusal on stderr never cuts a path or a long word in two, nor parts a path at
  a space in it. Under `error: no such directory`, a `--data` path of 128
  characters was cut across three lines, at `the-folder-this-` and before its last
  `/`, so it could not be pasted back; the `--only` refusals cut the config's name,
  the cards' folder and an empty archive's the same way, and `--discover`'s refusal
  cut a round label of 88 characters in two. Under folders named with spaces the
  same paths were parted at their spaces, and so were the two folders a run from
  somewhere else names. textwrap cuts a word longer than the line where the line
  runs out, and to it a path with a space in it is several words: eight of the
  fills in `build.py` passed one of the two flags that stop the cut, and the three
  in `tools/scheduled.py` neither. Such a word, and a path however its folders are
  named, now stands whole on a line of its own, as a long word does in the report,
  and one function fills every piece of advice the two write to stderr.
- A report field whose value is too long to share its label's line starts the next
  one, in the value column, as a sentence in the report does, and a path in it is
  one word however many spaces it has. Behind their labels, under a deep folder,
  `source` ran to 127 columns, `wrote        9 file(s) under` to 157 and the `file`
  a failed write names to 179, and a terminal folding the line put the rest at the
  left margin, where it reads as the next field; the fetcher's `from` and `into`
  ran to 194 and 135. Under folders named with spaces, `open` and the fetcher's
  `wrote`, which already wrapped, parted their paths at a space. A value that fits
  prints as it did. The fetcher's `next` is still one line: a command wrapped with
  no `\` is two commands to a shell.
- A value of more than sixty characters repeated back by a warning, a note or a
  refusal is named by its first dozen characters and its length, as in `round
  111111111111... (5000 characters) is declared with no fixtures`, in the sheet
  table's warnings and the team each names, the declared draw's notes and those on
  a round the cards drew, the finals warnings' sentences, `--discover`'s refusal, a
  card whose division and round are printed on different lines, and the fetcher's
  rounds and index mistakes. Echoed whole, a declared round labelled with 5000 ones,
  or an `after_round` or `played` typed so, was a line of 5008 or 5017 columns
  however the sentence round it was wrapped, and the fetcher printed such a round's
  count a word to a line, each indented 5008 columns, and its index mistakes on
  lines of up to 5074. A value of sixty characters or fewer reads as it did, but
  for the card's two lines, which were cut at forty with no sign of it and are now
  whole to sixty. Still printed whole: a round label read from a card, in the
  review's `cards` line, its `number = "..."` line to paste and under the finals
  warning; a team named in the line to paste under a sheet line that is no table;
  and a setting the config does not know, an alias or a namesake.
- `--discover --force` writes a season name that a `[seasons]` line gives into each of
  the season's competition sections commented out, under a comment saying that the
  line names every competition of the season and that removing the `#` gives this
  one a name of its own, however the line's key is spelt. It wrote the name live, as
  though the section had named it, and a section's `season =` outranks its season's
  line, so an edit to the line renamed nothing: on a three-season demo archive the
  edited name was in none of the site's files and the old one in five, the line keyed
  `2021-winter` or `2021-Winter`. Now the edited name is in all five. A section that
  names its own season is written live, as it was, and so is one whose `season =`
  repeats its season's line, which is what an earlier `--discover --force` wrote into
  every section it named: in a `config.toml` written by it, take that `season =` out
  for an edit to the line to reach the section. The README says so under Several
  seasons.
- `config.example.toml`'s `[seasons]` comment says what names a season in each case:
  the competition's own `season =`, else its season's line, else the season its cards
  print, else its folder's name made readable, `"2026 Winter" of 2026-winter`, a
  section no card has reached yet the same way; and that each night on the front page
  is headed by the name of its first competition. It said a folder with no line "is
  shown by its folder name", which only a section no card had reached was, by its key
  as its folder has it, `2023-winter`: cards printing `Season 1` were headed `Season 1`,
  and a season whose cards print none was headed by its key made readable. The README
  says the same under Several seasons.
- In multi-season mode a competition declared in `config.toml` that no card has
  reached yet is headed on the front page by the name its season would give a card:
  its section's `season`, else its season's `[seasons]` line however the key is
  spelt, else its folder's name made readable, `2027 Summer` of `2027-summer`. The
  front page read the section's `season` alone, so a section for `2023-winter` with a
  `[seasons]` line was headed `2023-winter - Tuesday`, the label in none of the
  site's files and `picker-data.js` giving it an empty `season_label`. The front page
  now asks discovery's naming rule and the season document's rule for showing it,
  rather than keeping an order of its own. So where its section or its season's line
  names the season, its first card leaves the heading as it was; with neither, a first
  card that prints a season's name heads it by that name, as it heads any card. The
  report's note on such a line says the label heads that section's card on the front
  page already, and that the season's pages wait for its first card, where it said
  the label waited for that card.
- A build that withholds players' names spends less time on it. Its check of what it
  was about to write searched every string it read for every name, although most
  repeat, as a team's name does in every match it played, and it now searches each
  once a run: 108 searches on the demo where there were 1,120, and 246 on the 10-team
  demo where there were 4,505. And each competition was copied whole before its names
  were swapped, although the swap builds everything it writes anew; it is no longer
  copied. Timed on the demo, the search took 18% of the time withholding took and
  takes 4% of it now, and on the 10-team demo 20% and 3%; the copy took 12% of it on
  each. Every file written is the same, byte for byte, and so is a refused build's
  report.
- `players = "initials"` gives each part of a hyphenated name its own initial:
  `Tui-Mae O'Dowell` is `T.M.O.` and `Pim Arkwright-Sole` is `P.A.S.`, where they were
  `T.O.` and `P.A.`, on a roster or filling in. A name `[player_names]` spells with an
  en dash is parted the same way. An apostrophe parts nothing, so `O'Dowell` is one
  initial, and two players who shared initials only because a hyphen was passed over,
  such as `Tui-Mae O'Dowell` and `Tui O'Dowell`, are no longer numbered `T.O. 1` and
  `T.O. 2`. Only those labels change: on the 10-team demo, one of its 30 players'
  labels, `Fenella Vasquez-Poole`'s, is `F.V.P.` where it was `F.V.`, and every other
  byte of the site is the same.
- A careers page split by initial has an index of names: each row of `careers.html`
  is the name and its link to the page for their initial, with no figures, which are
  on that page, in its own list and under the career. The index copied every
  career's seven totals, so over 30 demo seasons of 12 teams it was the largest page
  of the split, 213,592 bytes of the 29 pages' 1,910,303, and it passed 1.5 MB by
  itself at about 3,300 careers, before any page it was split into did. Now it is
  63,950 bytes, 127 bytes a row where it was 449, and the 29 pages 1,760,661;
  the largest initial's page passes 1.5 MB first, at about 5,000 careers, and the
  index at about 11,300. Every initial's page is the same, byte for byte, a player
  page's link to their career lands on it as it did, and a careers page that fits
  on one page keeps every figure.
- The split careers index opens with words true of the page. It opened with the one
  page's lead, which says every total is the sum of the rows above it and every row
  names its team and grade, over a list of names with no season row, team or grade,
  and one season in it said every total below was that season's. It now says each
  player's seasons are added up on the page for their initial, where every season
  opens the page its row was copied from, and that it lists the names only, then
  why the page is split. Each initial's page, and a careers page that fits, open as
  they did.
- A build works the careers out once. It added up every career and rendered every
  careers page twice, once to write them and again for the report's `careers` line,
  which read the split back off the disk the write had just changed: 0.132 s and
  0.120 s of a 6.05 s build over 30 demo seasons. The line is now read off what the
  write returned, and only `--check`, which writes nothing, and a build whose write
  stopped work it out on their own. The line says what it said, and the site is the
  same.
- Three messages that can name three things or more are worded for that many. One
  printed name in three teams' roster blocks was told `one player holds both
  registrations: whatever either of them played`, that `they are two different
  people`, and `If they are two people`. It is told `all 3 registrations`, `any of
  them`, `3 different people` and `all 3 registrations do belong together`. Cards of
  three shapes were told `One of them is missing or has gained a set row`, that the
  competition's cards need `splitting into two seasons`, and `Neither reading is safe
  to score`. They are told `Some of them are missing or have gained a set row`,
  `splitting into seasons` and `None of the 3 readings is safe to score`. A team with
  three matches in one round was told its points `would be counted twice`, and is
  told `3 times`. A fixture whose card is in data twice is one of the team's matches
  that round, named once, where it was listed twice and counted as another match.
  Each message counts its list once, and two of anything reads as it did, but for a
  team's two matches in a round, which are joined with `and` now.
- Twenty-one more lists read `a, b and c` where they were joined with commas alone,
  each after a colon, at the start of a sentence, or beside a count past a cap or a
  word for none. They are F9's fixtures, a team's matches in one round, players on no
  squad and no roster, and F10's teams behind the rest, `hilltop-aces,
  parkview-wanderers and riverside-rockets have fewer than the 3 counted by the
  rest.`. So are the cards a declared format disagrees with, the three notes on
  season folders, the team list read from the cards, and the cards the misread note
  names. So are a bye's declared teams, the old dashboards that print players' names,
  and the files an old dashboard holds that this tool did not write. So are the files
  under data that are not PDFs, `data/Round1/scorecard.html and data/draw.xlsx`, which
  past five ended `, and 2 more`; the competitions the reminder about unfinished sets
  names, which of eight had no `and` at all; and the fonts a page's text could not be
  read in. And so are the page turns `tools/doctor.py` reads, `tools/fetch.py`'s card
  lists and the leftovers it removes, and the browsers `tools/screenshots.py` looked
  for. Two of them agree in number now. A declared format's one card is told `the
  card describes` and `wrong with the card`, where it was told `the cards describe`.
  The misread note's one card `records` a set score where it said `record`, and two
  are told `the cards were misread`. `tools/fetch.py`'s list past its cap of eight
  still ends `and 1 more`. Three keep their commas on purpose: `--only`'s echo on its
  error line, `error: --only 2026-wintre, 2025-sommer, x: not seasons this run
  found`, which repeats what was typed, a finding's detail, and the report's
  `seasons` line, a field's value. A few lists this change does not reach keep them
  too, among them the rounds a declared draw does not list, the fixtures of a declared
  round with no card, and the files a dashboard folder holds that this tool did not
  write, in parentheses. On the demo only the team-list note and F10 change, in the
  report and in the season's files.
- A set rule, match format or scoring scheme that `config.toml` names and this
  program does not know is refused with the ones it could be and the nearest, as
  `unplayed` and `[build] seasons` are: `error: config.toml: [rules] scoring scheme
  "games-bonsu" is not "games-bonus", "rubbers", "sets-first" or "simple". Did you
  mean games-bonus?`, where it said `unknown scoring scheme 'games-bonsu'; known:
  games-bonus, rubbers, sets-first, simple`.
- Blocks outside the rules banner's *What these mean* share their words out across
  their lines too, where that leaves them no word or two of their own. In the demo's
  six views at 1280, 700 and 375, 32 ended on a line of 15 characters or fewer, 29 of
  them on a phone: the health strip's summary on `club's word` in every view, a tile's
  foot on `2`, the match page's heading on `Wombats`, a chart's line under its title
  on `axis.`, a cancelled match's facts on `— · 1 note`, and at 700 the matches page's
  lead on `been.`. Each kind of block was measured on its own. The banner's lines, the
  health summary, the ladder's note, a view's heading and lead, a chart's line, a
  match's facts, a bye's or a fixture's line and a table's caption are balanced, and a
  tile's foot is wrapped `pretty`, because balanced it broke a team's name across
  lines. 10 are left: five tiles' feet and the match heading, each within 3 characters
  of the most even split its words allow; three in a table's key, which balanced broke
  inside `D W-` / `L`; and a card's Source, a file's path. The heading is balanced all
  the same, because with nine teams it ended on `Galahs` and balanced it does not, and
  so is a bye's line, which ended on `match played` there. Every block keeps its number
  of lines, so no view is taller or wider, and the careers page and a browser that
  does not balance lines wrap them as before.
- The reader's own summary, which the doctor prints above its verdict, gives the box
  warnings the reading gives. On a card that draws a Forfeit label and box again below
  the table, the reading warned `labels name 31 of the 31 checkbox squares found,
  where 4 set rows need 30; the one more is a home Forfeit box in no set row, which no
  row reads`, and the summary said `warnings: none`. The summary now lists that
  warning, from the one function that writes it for the reading, so the doctor gives
  it once in its summary and once in its verdict. A card drawn as the template draws
  it is warned of no box, as before.
- A square no label names, on a card that names every box it needs, is warned of, by
  where it is. Such a card was read with no warning at all, and the reader's summary
  said `31 squares found, 30 named by a label` over `warnings: none`. The warning now
  says `the one more is a square on the home side in no set row, which no label
  names`, or `a square on row 2's home side`, and squares in one place are counted
  there, as `2 squares on the home side in no set row`. A card that named a box more
  than it needs as well, and was warned that `a tick on any box they do not name was
  not seen`, is now told where that square is instead.
- A card with a tick in a box nothing reads is refused, naming the box. A Forfeit
  ticked in a copy of its box drawn below the table, or a tick in a square no label
  names, was read as though nothing were ticked there: the build scored it with no
  error and published the match as four sets played. Such a card is now one F4 error,
  `this card ticks a checkbox this reader reads nothing from, so it would be read as
  if that box were not ticked`, whose detail says where the tick is, as `the tick is
  in a home Forfeit box in no set row, which no row reads`. The match is not published
  and the build exits 1, and `tools/doctor.py` gives the refusal a cause of its own. A
  box drawn again in its own row, or a whole-match flag's box drawn again anywhere, is
  read as it was, ticked or not. A tick is counted by its fill, and one a named box
  reads is that box's, so a ticked box whose outline is painted again a point off is
  read as it was, with the warning for the square, and one fill under a square
  painted twice is one tick.
- A folder under `data/` this account may not list is refused by its own name, and
  counted as a folder. With the demo season's `round-05` at mode 000 the rebuild
  exited 0 with nothing on stderr, `pages seen 12`, `scored 12`, `refused 0` and
  `exit 0: all 12 card(s) scored, no errors`, three `gone` lines the only sign of a
  round missing, and neither the page nor the front page flagged anything. At mode
  444 the whole season was one refused card named `data`, its detail naming
  whichever card was met first, and nothing was built. With every card behind such a
  folder the run said `pages seen 0`, advised exporting a match from Match Centre,
  and recorded that no scorecards were found. Now each such folder is refused once,
  under `refused` and beside any card refused with it, with `the directory could not
  be listed` and the system's reason, and the run exits 1 and builds everything
  else. How many cards it holds is what nobody knows, so wherever a card is counted
  it is counted apart: a `not opened 1 folder(s)` line under `refused`, `refused: 1
  folder(s) not opened`, `the run's 1 folder(s) not opened` on each competition's
  `problems` line, `exit 1: 1 folder(s) not opened`, `meta.folders_unlisted` beside
  an unchanged `cards_seen`, `1 folder could not be opened` on the front page, and
  `1 folder not opened` on the page's pill, detail and refused list. The doctor is
  offered for files alone, in the report and on the page, and not for a folder whose
  name ends `.pdf`. Every entry of `validation.rejected` gains `folder`. With
  nothing else read the ending is `exit 1: 1 folder(s) not opened, nothing built`,
  and the status reads `a file or folder it needed could not be opened`. A run with
  a folder shut lists no dashboard as left behind and `--prune` removes none: with
  the Tuesday division's folder shut, `--prune` had deleted its dashboard and exited
  0. The scheduler watches such a folder by its name, so opening it is a change, and
  an empty folder at mode 000, which built with exit 0, now fails too, since nothing
  tells it from a full one. The walk tests each entry itself, so Python 3.14, whose
  `Path.is_file` answers no where 3.12 raises, refuses `round-05` at 444 the same,
  exit 1 over `1 folder(s) not opened`. `data/` itself at 444, which ended `1
  card(s) found and not scored, nothing built`, now stops the run with `error:
  [Errno 13] Permission denied: 'data'` and exit 2, as at 000. A folder the system
  keeps for itself, `lost+found`, `System Volume Information`, `$RECYCLE.BIN` or a
  name starting with a dot, is passed over as before when it cannot be opened: with
  four of them at 000 in `data/` the run exits 0 over all 15 cards. With a card
  refused beside folders holding every other card, the status reads `a file or
  folder it needed could not be opened`, where it said the one scorecard found could
  not be scored, the report points to the section above once, and it ends `exit 1: 1
  card(s) found and not scored, 5 folder(s) not opened, nothing built`.
  `validation.rejected` lists the folders among the cards in path order, as the
  report does, and the page joins its clauses as the report joins a list: `1 card
  seen and not scored and 1 folder not opened`.
- A refusal says what is wrong, and leaves its file and its page to whoever prints it.
  Every refusal the reader gave began with the path, which the report, the page and
  `tools/doctor.py` print beside it already, and one about a page numbered it from 0.
  With a card at permissions 000 in the demo season, the list under `refused:` gave
  `data/locked.pdf`, `not a readable PDF`, then `data/locked.pdf: cannot read ([Errno
  13] Permission denied: 'data/locked.pdf')`, and a text file `data/minutes.pdf: not a
  PDF`. The doctor gave a two-page file with no `/Contents` as `page 2 of 2` over `This
  page cannot be read: data/both-no-contents.pdf: page 1 has no /Contents`. They now
  read `cannot read: Permission denied`, `not a PDF` and `This page cannot be read:
  the page has no /Contents`. A refused first page of a file with more pages, which
  the report and the page print as the file alone, is named in its detail, as `page
  1: the page has no /Contents`.
- A refused card's detail is printed once in the report and once on the page. F4 gave
  it as its own detail and the list of cards not scored gave it again, so on the demo
  season with a cut card, a text file and a locked card each detail came out twice in
  the report and twice on the page. F4 now gives the card and its reason, then `The
  list of cards not scored gives its detail.` where there is one, and the detail is
  printed under `refused:` in the report and under `seen and not scored` on the page.
  With the change above, the report names the locked card 3 times where it named it
  7. The exit is unchanged.
- An inline image whose bytes hold `EI` ends where the image ends. The reader ended it
  at the first `EI` among its bytes with white space either side, and never read the
  length PDF 2.0 lets its dictionary give, so a team list with an 8-byte image holding
  ` EI (` read 3 of its 9 words though the image gave `/L 8`, and one holding ` EI Q`
  was recorded as 2 images. An image now ends at its `/L` or `/Length` where an `EI`
  is there. Otherwise it ends at the first `EI` after which the page reads on as
  content, where none does at the first it cannot rule out, and else at the first,
  but never at one past the next image's `BI`. Both read all 9 words and record 1
  image.
- On the page, a refused card's path breaks over lines only where it is wider than the
  list. The item was a flex row, which made the path a column of its own, so in
  headless Chrome `data/truncated.pdf` broke over 2 lines at 1280px and over 9 of two
  letters each at 375px. The item is now running text: at 1280, 700 and 375px a path
  narrower than the list is on one line, and one 679px wide breaks over 2 lines at
  700px and 3 at 375px.
- The *Season progress* box counts the fixtures of the rounds it counts. Its round
  count and percentage are the regular rounds', and its foot counted the whole
  season's fixtures, finals and all: measured with a draw dating rounds 1, 2 and 3,
  round 3 a final, and a semi-final's card, the box read `Round 2 of 2` over `7 of 7
  fixtures played · 100% of the declared rounds`, where the two regular rounds hold 4
  of the 7. It now reads `4 of 4 fixtures played`, and with no length declared, `6
  matches scored` beside `3 rounds` where it said 7. The count is the build's, in
  `season.json` as `meta.regular_fixtures`, `played` of `total`: a fixture is played
  once however many cards it has, a washout is not played, a level night in a grade
  with no draws is, and a round nobody has sent a card for yet is still to play. The
  *Matches played* tile below the box still counts the whole season, `7` `of 7 in the
  draw`. A format `4` `season.json` built before the key has no count to give, and
  the foot shows `—` for it. The document format is still `4`. The first build after
  upgrading rewrites each competition's `app.js`, `season.json` and `season-data.js`,
  and the one-file page where one is built, and the next build writes nothing.
- The progress box says `1 match scored`. With a season length declared and one card
  it said `1 matches scored · 20% of the declared rounds`.
- A page given a `season.json` of another format than its own shows none of the
  season. Its data health section said that some values might be missing or mean
  something else, and every view was drawn beneath that anyway: measured on the
  demo's document rewritten as format `2`, the list of matches said `scheduled` for
  all 15 nights with cards, and as format `1`, a team's page said `counts for
  nothing` 5 times and `not played` 16 times. Every view now reads `Season not
  shown`, and the note says `The season is not shown` and still asks for `python3
  build.py` to be run again. The rules in force, and the teams and players to jump
  to, are still shown.
- A line with no rating under a squad's rated players that no set row names is now
  said on the card to be what it may be, a heading printed over that squad alone.
  The reader knows a fill-ins heading by the words `Fill-ins`, `Reserves` and
  `Substitutes`, by a bracket, a colon or a word with no letter in it, and by one
  line printed the same over both squads, and a heading in another word over one
  squad is none of those. Measured on cards from the card writer: `Emergencies`,
  `Substitutions` or `Spares` over the home squad only, with a fill-in under it, read
  5 and 3, the heading and the fill-in home players; with the fill-in under the away
  column, 4 and 4; over the away squad only, 3 and 5; and beside `Substitutions`,
  `Reserves` or `Fill-ins` over the other squad, 5 and 4. None had a note, and on
  both legs of one fixture there were 8 players where there are 7, `emergencies`
  among them, and 5 or 6 warnings where the template gives 3 or 5. Nothing on the
  line tells such a heading from a registered player printed with no rating, who
  read as a player in every shape, so the line is still read as one, and the card
  says `read 'Emergencies' on the home side as a player, though it may be a heading
  printed over that squad alone`: the first such line in each squad, one note for
  both, where the block does not end at a fill-ins heading and no heading has ended
  that squad. The squads and the season read as they did, and a withheld build keeps
  the note with the player's label in the name's place. The cost: a player printed
  with no rating at the foot of a rated squad who sat the night out, over no heading
  this reader knows, is quoted too, and a heading over a squad that prints nobody's
  rating is still a player with no note, whatever the other squad prints.
- A squad of five or more on a card from the card writer is read whole, and a roster
  line printed too close above the heading under the squads to be read is said on the
  card. The writer printed the fill-ins headings where a template of three roster
  rows prints them, however long the squads, and the squads are read from a band
  ending a point above the heading's top: a fifth row's centre was at 515.57, under a
  band ending at 513.91. Squads of 5, 6 and 8 read 4 with no note, on either side,
  and on both legs of one fixture the Alphas' squad of 5 gave 7 players where there
  are 8. The writer now prints the headings one pitch under the longer squad's last
  row where that is lower, the fill-ins under them, and squads of three and four byte
  for byte as they were, so the demo's cards and dashboards are unchanged. The band
  is as it was: widened a point into the heading, the least that holds a fifth row,
  it still reads a squad of 6 or 8 as 5, since the rows after the fifth are printed
  under the heading, and widened to the heading's baseline it reads `Fill-ins` over
  one squad, and `Comments`, as players. A line under the band and above the
  heading's line, rated or not, in a squad no heading or dash has ended, is left out
  as before, and the card says `left 'Juliet Jardine' on the home side out of the
  squads, since it is printed too close above 'Fill-ins' to be read as a roster
  line`, or above `Comments` where no fill-ins heading is printed; a withheld build
  keeps the name off its pages. `Comments` has not moved, so the writer's first
  fill-in under a squad of eight is printed on its line, and from nine rows the foot
  runs into it (filed).
- A career's table fits the careers page on a laptop. Headed in words, `Rating
  (singles / doubles)` and `Doubles won` among them, 106 of the 107 tables on a
  ten-season demo's page ran past its 1,040px column at 1280, the widest 1,410px,
  and 19 of the two-season demo's 20. They are headed as a season's tables are now:
  a short label, its words in the heading's title, and a key in the caption under
  its sentence, `M — Matches played · Won — Sets won · Lost — Sets lost · …`. None
  overflows at 1280 or 1024, the widest 907px; at 768 and narrower they scroll, as
  a season's tables do. A figure the season's players table heads too is headed as
  it heads it, `M`, `Won`, `Win %`, `GF` and `GA`, and no label is one the season
  pages give another meaning: there `W` is a match won. The key puts two lines
  under each caption, so the ten-season page is 28,624px tall at 1280 where it was
  26,059 and 351,521 bytes where it was 295,211. A career is about 3,850 bytes of
  one page where it was 3,322, so the one page holds about 389 careers before it
  is split where it held about 451, and a club between the two is split by its
  next build and stays split. Thirty demo seasons, 462 careers and split already,
  report "As one page it would be 1.8 MB" where they said 1.6; their 29 pages are
  2,010,445 bytes where they were 1,760,661, T's page 164,378 where it was 142,388,
  and the largest initial's page passes 1.5 MB at about 4,400 careers where it did
  at about 5,000.
- The careers list sets accents aside when it orders names, as it does when it
  files them under an initial, and lists them in the order of the pages it files
  them on. It set case aside alone, so `Émile` was under E and listed after `Zoe`,
  on a split site `careers-e.html` listed Eve and Ezra before `élodie` and Émile,
  and `4 Tien` and `'Ofa Renn`, filed under 0-9 and O, came before `Adam`. The list
  goes from a to z, then 0-9, then other, alphabetical from each name's initial,
  and two names that fold alike are ordered by their slugs, so a rebuild is the
  same bytes. A slug, a career's initial, the list's order and the matching of a
  withheld name are folded by one function now. The slug's fold and the withheld
  names' had differed on 700 characters, and the slug's dropped a superscript or
  mathematical letter, so `ᴬda` slugged to `da`; it is `ada`, and the dashboard
  slugs a club's name the same way for the key it keeps its light or dark choice
  under. The demo's names have none of these, so its careers are listed in the
  order they were and its slugs are the ones they were.
- The careers and withheld-names tests pass in whatever order their classes are
  run. Unittest sets a module up again when another module's class has run between
  two of its own, and both kept what their first setup built, paths into a folder
  its teardown had removed: 4 of 25 careers tests errored with a class of another
  module between them, and 2 of 12 withheld-names tests. The careers tests made
  their folder on import, too, so every process that imported them for their page
  reader made one.
- The note on a team list read from the cards says what to do with the file as it
  stands, where it said `Check it, then remove the # from teams = [...]` of every
  file. A config.toml written before any card holds no teams line, and was told to
  remove its #, while the same run's review said the list was `not in it`. So was a
  `--check` run, which writes no file, and a file whose list is a live `teams = []`.
  The note now asks the file what the review asks it. A list behind a # is told to
  remove it, as before. A list that is not there is told `add [competitions.<id>] to
  config.toml, and teams = [...] under it`, or to add only the list where the file has
  that table already, and a live empty list is told `fill in teams`. A table is found
  as the loader reads it, so one written `[competitions."<id>"]` is the table, and one
  behind a # is not, and a list behind a # under a header behind one too is told to
  remove the # from the header as well. F8 says only that the team list was derived
  from the cards, so an unknown team can only be reported as a suspicion, and leaves
  the step to the note. It said ``Uncomment `teams` in config.toml`` in every case.
- Every sentence that sends a club to a line of its config names the file the build
  read. Under `--config other.toml` the rules' footer said `These come from
  config.toml`, F8 said ``Uncomment `teams` in config.toml`` in the report and in
  season.json, and a bye the draw also fixtures, `[namesakes]`, a player on two teams
  and two seasons on one ladder named config.toml too. The build of the demo's two
  seasons named it on 3 of its 136 lines, and now names it on none. The footer says
  the scoring and the sets are never read from a card, then `set yours under [rules]
  in other.toml and re-run`. The demo's own build is not given that second sentence,
  since the demo writes its file again each time it runs. A config file named for a
  player is refused as before, and is told `Rename the file, and give its new name to
  --config.`, where it was told to rename the title, the club or a competition in
  config.toml. While the file's name holds a name, the advice for the other places
  says `your config file`. What the build publishes, a finding in season.json and the
  front page's `declared in`, names the file by its name alone, so a config given as
  `--config "club files/our.toml"`, or by its whole path, puts none of its folders on
  the site. The report's footer gives it as `--config` did, as its notes do.
- Under `--redact`, the doctor gives the reason a page was refused as the reader wrote
  it, and scrubs its detail alone. Both were scrubbed with the rest of the report, so a
  page printing a word the reason uses had it shaped out of the reason: a card ticking
  a box nothing reads, whose comment said nothing reads a box ticks like that, was
  refused as one that "<5 chars> a checkbox this reader <5 chars> nothing from, <2
  chars> <2 chars> <5 chars> <2 chars> read as if <4 chars> <3 chars> were not
  ticked", and the summary's row for it read `<5 chars>: NOT READ`. The reason is now
  published as written in the verdict and in the summary, which gives a stage's detail
  on a line of its own beneath its reason. The reader marks which lines of its summary
  are reasons, so a line a page prints inside a word, as a name can through its font's
  map of its text, is scrubbed whatever it says. And a test reads every refusal the
  reader raises and fails a reason that interpolates anything but a count, a position,
  an index or a constant of the reader's, following an index to every call that passes
  it, so that no reason can come to quote the page.
- Under `--redact`, a folder or a file named like a word of the report no longer takes
  that word out of it. Every part of the card's path was withheld as a word, so a card
  under `pages/after/` had its report's `pages` row read `<5 chars>         1`, and one
  whose table rules are outlined was told that another program "re-printed it <5
  chars> it was exported"; and `banner.pdf`, a card with no home team, was told "the
  team-name <6 chars> does not straddle the middle of the table". The path is withheld
  whole, as given and as resolved, which is how the report prints it, and the words
  are kept.
- Under `--redact`, a name the report escapes is withheld as it is escaped. The summary
  quotes a set row's players as Python quotes a string, which writes a soft hyphen or a
  zero-width space inside a name as `\xad` or `\u200b`, so a card carrying one in each
  surname had each published whole three times. Each thing the doctor withholds is
  withheld in that form as well, which for a name without such a character is the
  name, so no other report changes: the demo season's 15 cards give 30 reports, plain
  and redacted, that are byte-identical.
  No name is printed in a case or with accents other than the page's, so the scrub
  still matches the case the page printed, and keeps `club hint` beside `Club`.
- A scheduled build that puts players' names back into a dashboard that withheld them
  exits 1, and says so on stderr. `tools/scheduled.py` exited 0 with its line on
  stdout, which the cron line it prints sends to `/dev/null` and `pythonw.exe` has no
  stream for, so a public copy scheduled with a settings file that had lost `[build]
  players` published every name and told nobody: run twice as cron runs it, it
  exited 0 and printed nothing. That run now exits 1 however clean the build was (2
  when the build could not run), prints its line on stderr, which cron mails, and
  a clean build's report says `exit 1 (build.py exited 0, but players' names are
  back)`. Each run that finds nothing new after it exits 1 too and says it again,
  ending `until that build`. The next build finds no dashboard withholding names,
  and its status is the build's alone. `reports/last-run.json` now records where the
  names went back, so a record an earlier version wrote costs one build. Names an
  earlier version put back are not said again: that build finds no dashboard
  withholding them, and only the report of the build that put them back says so,
  on its `players` line.
- `tools/scheduled.py` refuses an empty `--data`, `--out`, `--config` or `--reports`
  at exit 2 in one line after its usage, before it makes anything, and prints no
  schedule for one. An empty path is the project folder: `--config ""` failed on
  every run with `error: .: cannot be read`, and `--print-schedule` printed a line
  naming the project folder as the settings file. Beside folders of their own, an
  empty `--data` read the project folder as its cards, an empty `--out` wrote the
  dashboard in beside `build.py`, and an empty `--reports` kept the reports there,
  each at exit 0.
- A card that lost the label of a box printed once for it, *Match Cancelled* or *Match
  Disputed*, is refused as a copy that lacks part of its page, and the doctor says to
  download it again. With every other label on the page the template's own, it was
  refused as a card printed from another template, and the doctor blamed one that words
  its labels differently or prints them away from their boxes. Such a page cannot be
  told from a template that words that one label another way, as *Match Contested*,
  which is now refused the same way, and a copy downloaded again tells them apart: if
  the new copy is refused the same way, the verdict says to send it to whoever
  maintains this tool. So too a card that kept either word of the label and lost the
  other, and one whose lost label's box is ticked, whatever its comments say. A label
  printed beside its box with something else on it, as *Disputed:*, a label of two
  boxes read nowhere, as *Match Won*, and a label lost where a square sits off its line
  are still refused as another template's.
- A card printed with each set's box to the right of its label is told so. Its refusal
  named *all 8 Forfeit boxes* and said a square is named by Forfeit only within 16.1pt
  of the label and on the label's left, when each box is 2.67pt from its label, and the
  doctor blamed a template that prints its labels away from their boxes. It now says
  *this page prints the set's boxes right of their labels: the square nearest every
  Forfeit, Retired and Incomplete label on its line is on the label's right, and a
  set's label names only a square on its left*, the summary's checkbox line ends with
  its first half, and the doctor gives a template that *lays them out another way*. It
  is said only where, for every set's label with a square on its line, the nearest
  one is on its right, so a *forfeit* in the card's comments does not take it back. A
  card whose Forfeit squares sit 2pt off their line has no square left of each Forfeit
  label and the Retired square on its right as well, and is still told of the reach.
  The card is still refused rather than read by looking right, and a Match Cancelled
  or Match Disputed box lost with it keeps its own clause. A card printing only the
  home side's set boxes right of their labels is still told of the reach (filed).
- A card with a set's label printed off the set table beside a *Match Winner* or *Match
  Forfeited* box is read, where the flag's own label reaches its box. A stray *Retired*
  printed 3.3pt right of the away side's Match Winner box, which its own label is
  14.17pt from, took the box: the card was refused as a copy that lacks part of its
  page, saying *every square it finds is named by the label printed beside it, so a box
  it could not name is one whose square it cannot find; the one it could not name is
  the away Match Winner box*, and the doctor said to download it again, though the box
  is drawn. So did a stray *Forfeit* or *Incomplete*, and one beside the away Match
  Forfeited box, where a forfeit ticked is now read as a forfeit. A set's label off the
  table now names a box only after every other label has named its own, so a set's box
  drawn again off the table is still warned of, and refused ticked, as one no row
  reads. Where the flag's label is lost as well, and the stray still names its box, the
  refusal no longer says only that the box's square cannot be found: it adds *or one
  whose square is named as an away Retired box in no set row, which no row reads*,
  naming each set's box off the table on the flag's side. A card short of a set's box,
  whose square is in its row, is still told only that its square cannot be found. A
  stray flag's word, as *Match Forfeited* printed beside the away Match Winner box,
  still takes the box (filed).
- A whole-match forfeit whose set rows give the match to the side its Match
  Forfeited box says forfeited is one error, `forfeit-contradicted`. With the away
  box ticked over rows 2-6, 3-6, 4-6, 1-6, a card printing no verb and the away
  Match Winner tick, or no tick, built at exit 0 and was paid as a forfeit to home,
  5.0 points to 4.4 under `games-bonus`, its tick check saying it agreed with the set
  rows. What such a card got turned on what else it printed: `DEFEATS` was F6 on the
  verb, `LOSES TO` a `verb-mismatch`, and the home tick beside `LOSES TO` both. The
  rows are read by the club's own rules, as the build reads a played card: every row
  a set finished under the `sets` rule, more sets to the side that forfeited than to
  the side that came, and the match to it as the scoring decides. The detail says
  so, `sets 0-4 and games 10-24 decided on games then sets say away`, then whom the
  box gives the match to and which of the verb, the tick and the TOTAL row go with
  each. On a card it reports, a verb or tick restating the box is named in it rather
  than reported by F6, `verb-mismatch` passes by a verb for the side that forfeited,
  and a TOTAL row totalling the box's award, 4-0 and 24-0, is named in it where it
  was a `totals-mismatch`. A draw verb says what neither the rows nor the box does,
  and is still a `verb-mismatch` beside it. Of 72 cards probed under `games-bonus`,
  the 24 whose rows give the side that forfeited every set had 4 built clean, 16 one
  error and 4 two; 18 are one error now, and the 6 printing `DRAWS WITH` two. The
  other 48 build as they did: the away box over rows 6-4, 4-6, 0-6, 6-5, which give
  it the games but not more sets, over 6-0, 4-6, 4-6, 5-6, whose games 19-18 give
  home the match, or over rows for home, and rows for away with no box. Rows with a
  set the rule does not finish are not asked, and build as they did too: a forfeit
  given up part way, 2-6 and 3-3 with two rubbers unplayed, a rubber retired at 3-1,
  rows 4-0, 3-4, 3-4, 2-4 under `first-to-6`, and the filed card's rows under
  `pro-8`. So do rows left blank and every played card. Rows 6-0, 4-6, 4-6, 5-6 with
  the away box are reported under `sets-first`, which decides on sets. On rows the
  build fails this way `tools/doctor.py` names the set rules, and the scorings where
  not all four, under which the build reports the card as `forfeit-contradicted`,
  where on the card this was filed for, whose checks all agree, it said nothing.
  Under those rules it says what the finding names, `as the verb 'LOSES TO' and the
  away Match Winner tick do`, and that a tick the reader hands to the verb is
  reported by `forfeit-contradicted` there and by the verb elsewhere. That a
  cross-check disagreeing is an error it says only under the rules where the build
  still reports one apart, and not at all where there are none. It said both under
  every rule: of 101 cards probed under all 32 set rules and scorings, 15 said the
  disagreement was an error, and 5 of them that the verb reported the tick, under a
  rule where the build makes only `forfeit-contradicted` of it. None does now. The
  reader's own summary above the verdict says a tick's hand-off as the verdict does,
  where, on 79 of 679 cards probed, it said the verb reported the tick under a rule
  where the build does not. A card builds as it did where it exits and is reported
  as it was, a `verb-mismatch` on a forfeit worded as the next entry says. The demo
  seasons build byte for byte as they did.
- `verb-mismatch` on a whole-match forfeit names the Match Forfeited box by the side
  it is ticked against, and says whom it gives the match to: `verb 'LOSES TO' says
  away; the away Match Forfeited box gives the match to home`. It said `the Match
  Forfeited tick says home`, under a message saying away forfeited, and a tick that
  says home reads as one for home. The message is as it was.
- Lists in brackets, after a dash or after a word for rounds read `a, b and c` too,
  and so does the report's last line, where each was joined with commas alone. Among
  them are the three the twenty-one lists above left with their commas: the rounds a
  declared draw does not list, the fixtures of a declared round with no card, and
  the files a dashboard folder holds that this tool did not write. F3 says `3
  regular rounds have been played (1, 2 and 3). Cards were found for rounds 2 and 3,
  which the draw does not list`, and discovery says `cards were found for 3 regular
  rounds (1, 2 and 3) and 2 finals rounds (SF and GF)` and `round 1 is built from
  cards played in more than one year (2025 and 2026)`, which beside F9's `2025 and
  2026` said `(2025, 2026)`. So do the rounds of a declared draw with no cards, the
  rounds read as finals, the rounds the comment `--discover --force` writes says a
  draw does not list, two set rows on one slot, the names on a side with too many or
  too few for its set, the numbers in one games cell, the page templates and this
  tool's own files. The report's `cards` field says `15 across rounds 1, 2, 3, 4 and
  5` and `2 across round 1`, where it said `round(s)`. Its last line lists its
  reasons as the refused heading above it does, `exit 1: 1 card(s) found and not
  scored, 1 folder(s) not opened and 1 validation error(s)`, and so does
  `tools/fetch.py`'s. `and` is three columns wider than a comma, so a long last line
  folds a line sooner. A season typed as `rounds_total = 0` over one round of cards
  is told `1 regular round has been played (1)`, where it was told `1 regular rounds
  have`. The shapes the config.toml a first run writes names are offered as a
  choice, `"singles-6" or "singles-doubles"`, as a setting's values are, and
  config.example.toml offers them so too, where it said `One of: doubles-2, ...,
  singles-doubles.` The notes on a match shape read off the cards or declared list
  its rows as a sentence, `Doubles 1, Singles 1, Singles 2 and Doubles 2 (first to
  6)`. The report's `format` field and the page's keep their commas, as the scoring
  field keeps `win 4, draw 3, loss 2`. On the demo only the report's `cards` line,
  its note on the match shape and the written config's shapes change.
- The scheduled wrapper's and the command line's tests pass in whatever order their
  classes are run. Unittest runs a module's cleanups when a run moves on to another
  module, and sets the module up again when it comes back, and each kept the path of
  a folder its cleanup had removed. A run of 203 tests, 91 of them the scheduled
  wrapper's, with classes of two other modules between theirs, had 7 errors, and a
  run of 12 had 4 of its 7 command-line status tests in error with a class of
  another module between them. The fetcher's tests kept a list the same way, and it
  is emptied with the others. A test reads every test module and fails a list, dict
  or set filled where a module cleanup removes a folder, unless it is emptied beside
  that cleanup or in `tearDownModule`. A clear registered as the module is imported
  runs only at the run's first change of module, and does not count.
- Every command's parser refuses its arguments within the report's 78 columns, at
  any terminal, at exit 2 under its usage as before. argparse printed the refusal on
  one line, and the project's own checks of the arguments with it: `build.py --prune
  --only` was 117 columns, `--players first` 106, `tools/scheduled.py --keep 1` 112,
  and a path with spaces in it that a command does not take 133 to 154. It laid the
  usage out at the terminal's width, 104 columns for `build.py` at a terminal 120
  wide, and `tools/demo.py`'s usage printed its list of formats at 149 columns on
  every refusal. The six commands now read their arguments with one parser, in
  `matchcentre/cli.py`. Its usage and help are laid out at 78 columns, and a list of
  choices is named in the usage by a word and listed in the help. A refusal is filled
  after the usage with no word cut, and an argument typed with spaces in it is kept
  whole however argparse repeats it: as typed, as the value after `--page=`, or
  quoted with each backslash doubled. What `tools/demo.py` and `tools/fetch.py`
  refuse after reading their arguments, such as a demo `--out` inside `data/`, is
  printed as before.
- Every line `tools/doctor.py` prints keeps to the report's 78 columns, with
  `--redact` and without, but a word too long for any line, such as a path or a
  font's name, which is printed whole on a line of its own. The report printed each
  line as long as it came: over 32 test paths, each read plain and with `--redact`,
  827 lines were wider, the widest a refusal's detail of 621 columns, and a check in
  the verdict's table ran to 433. A line too long goes on under a hanging indent, so
  each row still begins with its label: a row under its value, a line of the
  reader's own summary under the words after its label, a check in the verdict's
  table under its state, a row of the rect census under what it calls the rect, and
  a refusal, its detail, a warning and each of the verdict's paragraphs under its
  own indent. Three of the paragraphs were broken at 74 columns, and now break at
  the width, their words as they were. A header field's value too long for its line,
  such as a long match id, goes on under the field's name. Each font is named on a
  line of its own. A line is laid out after `--redact` takes the names out, so no
  name is parted across two lines and a path with spaces in it is withheld whole.
  Laid out first, such a path would be parted across two lines, and the scrub, which
  looks for it whole, would miss it. So a report is printed once its file has been
  read, as a `--redact` one always was.
- `tools/doctor.py` no longer says a page on which it reads no text and no rectangle
  may be empty, or its content inside a structure it skips, `a form XObject, or a
  compressed object stream`. It reads a form XObject, and refuses an object stream
  before it reads a page. It says the page draws nothing it reads a card by, and
  that it may still show a card in what it never reads: annotations, where a form
  filled in on screen keeps its fields, or letters and lines drawn as outlines. It
  advises exporting the card again from Match Centre, and sending the verdict to
  whoever maintains the tool if that export is refused the same way, where it
  advised nothing. A page whose only text is a form field's, which a viewer shows
  and `pdftotext` reads, is read as 0 words and 0 rects, and so is a page whose one
  letter and box are drawn as outlines.

### Requirements

- Python 3.11 or newer, for `tomllib`. The standard library only: no `pip
  install`, no build step, no server, no network access at run time. `build.py`
  checks the version on startup and explains the problem if it is too old.
