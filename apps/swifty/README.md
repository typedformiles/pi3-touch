# Swifty

Taylor Swift games on the HyperPixel (480x800 portrait). The games run along the bottom:

| Game | What it is |
|---|---|
| **Swiftle** | Guess the day's song title, letter by letter |
| **Quiz** | Rounds of ten questions about the songs: which album, which track, what year, finish the title |
| **Showdown** | Two songs at a time: pick the one you love more, and it ranks every song and era |

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Swifty** from the menu (page 4). It opens on the game played last.

## Swiftle

Views along the top: **Today**, **Practice** and **Stats**.

- **Today:** everyone gets the same song on the same day (Swiftle #1 was 19 October 2026),
  and no song comes round again until all 170 have had their day. The title is shown as
  tiles; punctuation, numbers and `$` signs are already showing. Tap letters on the
  keyboard. A letter that's in the title turns the era's colour; one that isn't goes dark
  and takes a bead off the bracelet. Six wrong letters and the game is lost.
- **Clues** open as the wrong letters add up: the era's colour after 2, the year after 3,
  the album after 4 and the track number after 5. The colour is the one fans link with the
  era (Fearless gold, reputation black, Midnights navy...), shown and named in the clue;
  the whole screen also takes on the era's shade.
- **When it's over**, it shows the song, album and year, the streak, and how long until
  the next one. A win gets sparkles.
- **Practice:** as many games as you like, from one era or any. It doesn't repeat the last
  30 songs or give away today's, and practice games don't count towards the streak.
- **Stats:** games played, the share won, the current streak and the best one, a chart of
  how many wrong letters each win took, and the last few days' songs.

Leaving the app, or the Pi losing power, doesn't lose a game half played. Everything is kept
in `/var/lib/pi3-touch-swifty/swifty.json`. A day counts towards the streak once it's
finished, won or lost; a day missed ends the streak.

## Swiftie Quiz

Views along the top: **Play** and **Scores**.

- **Play:** choose all eras or one ("Change ›"), then **Start**. A round is ten questions,
  made up from the song list so there's always a new one:
  which album a song is on, which song is (or is NOT) on an album, track numbers, which
  song opens an album or comes first on it, what year an album came out, which came out
  first, which era is which colour, and finishing a song title with its missing word.
  One-era rounds ask only about that album.
- Tap an answer: it turns green if it's right, red if not (and the right one turns green),
  with a line to remember it by ("Track 2 on folklore (2020)"). **Next ›** moves on.
- The end shows the score, a verdict (10 is *Mastermind!*), whether it's a new best, and
  every question with its answer. A round half played carries on after leaving the app.
- **Scores:** the best round and rounds played for all eras and each era, and the share of
  answers right overall.

## Song Showdown

Views along the top: **Battle**, **Top songs** and **Eras**.

- **Battle:** two songs, each on a card in its era's colour. Tap the one you love more; it
  glows gold as the winner, then the next pair comes. **Skip** if you can't choose (no
  points), **Undo** takes back the last pick. Tap "All eras ›" to battle within one era.
- **How it ranks:** every song starts level, and a win takes points from the loser - more
  for beating a song rated higher (the Elo system chess uses). Every song gets a battle
  before any gets a second, and so on, each against a rival rated near it, so the ranking
  sharpens the more you play. About 85 battles give every song one go.
- **Top songs:** every song that has battled, best first (gold, silver and bronze for the
  top three), ten to a page.
- **Eras:** the albums ranked by their songs' average rating, each with its top song.

## Songs

`songs.json` holds Taylor Swift's 12 studio albums, standard editions, with their
release years, track lists and each era's colour as fans know it (named, plus a lighter
or darker shade for the app's keys and buttons where the real one wouldn't show up on the
dark screen - reputation's black, Midnights' navy). Titles are written as on the album,
without features or "(Taylor's Version)". Two are left out: *22* (no letters to guess)
and one with a swear in it. There's no network: no Taylor Swift data service exists, and
the albums don't change. When a new album comes out, add it at the end of `songs.json`
with its year, its era's colour and its tracks, then deploy.

To check the list and see how long the next week's titles are (on the Pi, or on a Mac
from the repo):
`python3 /opt/pi3-touch/apps/swifty/swifty_data.py --check`

## Service

```bash
journalctl -u pi3-swifty -f        # live logs
sudo systemctl restart pi3-swifty
```

## Off-Pi

`python3 -m unittest discover tests` covers the song list, Swiftle's rules (wrong letters,
clues, the song of the day and the cycle), streaks, practice, Showdown's ratings, pairs,
undo and rankings, the quiz (hundreds of rounds checked: four different answers, the right
one really right, no decoy word that makes a real title), scores, and progress kept across
restarts. When pygame is installed, its screen
tests also play by tapping, check that every title fits its tiles, check text reads on every
era's colour, and draw every view.

On a Mac with pygame, `python3 apps/swifty/swifty_display.py` opens it in a window. You
can type letters as well as click them.
