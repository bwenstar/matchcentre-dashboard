# Security

This program has no server, no accounts and no passwords. One command,
`tools/fetch.py`, opens network connections, and it can open them only to this same
computer; see [The one connection it can open](#the-one-connection-it-can-open). It
reads PDFs off a disk, writes HTML beside them, and a browser opens the result
from `file://`. Most of what a security policy usually covers therefore does not
apply here, and saying so plainly is more use to you than a page of boilerplate:
nothing is listening, so there is nothing to reach.

What there is instead is two privacy promises and one network promise that
can be broken, and four places where the contents of a file decide what the
program does.

---

## The promises that can be broken

```bash
python3 tools/doctor.py --redact path/to/the-card.pdf
```

That prints the full diagnostic report with every name, team, club, venue, date,
rating, match id and file path replaced by its shape — `Priya Halloran` arrives as
`<2 words, 15 chars>` — while keeping every measurement, count and score. The
whole point of the flag is that the result is safe to paste in public, and the
README and both issue forms tell people to do exactly that.

**So if a redacted report still contains a real name, that is a security bug, and
it is the most serious kind this project can have.** Nothing was broken into:
somebody trusted the flag and published their club's membership, and an issue
tracker is public forever. The same goes for anything that gets identifying text
out by a route that was meant to shape it — a report line composed after the
scrub, a value the redactor never learned because the page did not parse, an error
message quoting bytes off the card.

Please report that privately, and please do not paste the leaking line anywhere
public, including in the report about it. Which line of the report it appeared in
and what kind of value it was is enough to find it.

**A dashboard built without names.** With `[build] players` set to `numbered` or
`initials`, or `--players` given, no file under the output folder, the front page
included, may contain a player's name, spelling or id. One that does is the same
kind of bug as a leaking `--redact`: report it privately, and quote the build's
`== players: no dashboard written` section if it printed one. That section gives
shapes, never names.

---

## The one connection it can open

`tools/fetch.py` copies scorecards from an index into a folder. It is the only part
of this project with network code, and that code can reach one computer, this one:

- it connects only to an address written as a number that belongs to this computer
  — an address beginning `127.`, or `::1` — and refuses every name,
  `localhost` included, before anything is looked up;
- it refuses a `file:` path that begins with two slashes or backslashes, which
  Windows reads as a folder shared from another computer;
- every card an index lists is read from the same place as the index;
- it sends no user name, password or cookie, follows no redirect, and never uses a
  proxy, whatever its environment says;
- what it receives is checked in a temporary folder, and only scorecards printed
  under the letterhead it was given are written into the folder it was given.

**So a way to make `tools/fetch.py` contact any other computer — a name it looks up,
a redirect it follows, a proxy it uses, an index that sends it elsewhere — is a
security bug**, and so is a way to make it write another club's card, or anything
that is not a scorecard, into that folder. Report either the same way as a name
surviving `--redact`.

---

## The four places a file decides something

**A card that makes the reader do more than read.** `matchcentre/pdf.py` is a PDF
parser written from scratch, and it runs over files that came from a website. A
crash on a malformed one is an ordinary bug — there is a whole test file of them,
`tests/test_hostile_input.py`, and the standing rule is that a bad file is refused
in a sentence rather than costing a club its build. It becomes a security bug when
a small file costs unbounded memory or time, or when the reader touches a file it
was not given: reading something else off the disk, or writing outside the folder
it was pointed at.

**A card that decides a path.** Team, round and player names read off the page
become slugs, and slugs become directory and file names under the output folder. A
card whose text escapes that folder, or overwrites something beside it, is a
security bug rather than an untidiness, because the text came from outside.

**A card that becomes markup.** The dashboard is HTML and JavaScript generated
from card text, and a club publishes the result — often on a domain with other
things on it. Every name has to arrive in the page as text. A name carrying a tag
or an event handler that becomes live markup is stored cross-site scripting in
whatever else lives on that domain.

**An index that decides what is asked for.** The index `tools/fetch.py` reads
chooses which files are requested and what they are called on disk. A name is a
match id of letters, digits, `-` and `_`, so it cannot climb out of the folder; an
index that makes the fetcher request anything outside its own origin, or write
outside the folder it was given, is a security bug.

---

## Not in scope

- **Your own PDFs and the dashboard built from them.** The tool writes where you
  told it to and reads nothing else.
- **Anything that needs the attacker to already run commands on the machine.**
  They can read the cards directly; nothing here is a barrier to that and nothing
  claims to be.
- **The dashboard having no login.** It is a folder of files: whoever can open it
  can read it, which is a property of publishing rather than a fault.
- **A dependency's vulnerability.** There are no dependencies — the program is the
  Python standard library and a test enforces that. A fault in Python itself
  belongs upstream.

---

## How to report

Privately first, please.

1. On the **Security** tab of the repository you got this from, choose
   **Report a vulnerability**. That opens a draft advisory only its maintainers
   can read, and you can write in it freely without publishing anything.
2. If that button is not there — the owner of the copy you are looking at has not
   turned private reporting on — open an issue saying only that you have a
   security report and asking for a private channel. **Put none of the detail in
   it**, and none of the output.

No address and no link is written down here, on purpose. This file travels into
every fork, and a link would send your report to somebody else's copy of the
project, whose maintainer cannot fix yours and should not be reading your club's
names. The Security tab is in the same place on every copy, including yours.

### What helps

- **What you ran and what came out, with the identifying part described rather
  than quoted.** "The roster line printed a surname in full" is actionable; the
  surname is not.
- **A card that reproduces it and names nobody.**
  `matchcentre/cardwriter.py` writes a scorecard PDF that the reader reads exactly
  like a genuine export, so a leak can nearly always be shown with invented names.
  A short script that writes the card and shows the fault is the ideal report: it
  becomes the regression test, and it lets a fix be proved rather than assumed.
- **Your Python version and platform.** The first line of the `build.py` report
  has both.

### What never helps

A real scorecard. It names both teams, every player who took a court, their
ratings and the two captains, and it cannot be un-sent. If the fault genuinely
cannot be reproduced without one, say so and ask — there are ways to get a file to
somebody that are not an attachment on a public tracker.

---

## What happens next

One person maintains this, so the honest answer about timing is days rather than
hours and occasionally longer. What you should expect: an acknowledgement saying
whether it is understood and reproduced, a fix in the next release, and a line in
`CHANGELOG.md` saying what it was. Say whether you want to be named there; the
default is to credit you.

There is one supported version, the newest release. There are no maintenance
branches and nothing is backported, so a fix means upgrading rather than patching.

---

## If you have forked this

The policy came with the fork and now points at you. Two minutes makes it true of
your copy: turn on private vulnerability reporting in your repository's settings,
so that the button in step 1 exists, and read the sections above as a description
of your build rather than of somebody else's. Your reporters will be writing to
you, and it is your club's names that are at stake.
