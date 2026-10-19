# Swifty

Taylor Swift games on the HyperPixel (480x800 portrait). The games run along the bottom:

| Game | What it is |
|---|---|
| **Swiftle** | Guess the day's song title, letter by letter |
| **Quiz** | Coming soon: which album, which track, what year |
| **Showdown** | Coming soon: two songs, pick the one you love more, and it ranks them all |

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Swifty** from the menu (page 4). It opens on the game played last.

## Swiftle

Views along the top: **Today**, **Practice** and **Stats**.

- **Today:** everyone gets the same song on the same day (Swiftle #1 was 19 October 2026),
  and no song comes round again until all 170 have had their day. The title is shown as
  tiles; punctuation, numbers and `$` signs are already showing. Tap letters on the
  keyboard. A letter that's in the title turns the era's colour; one that isn't goes dark
  and takes a bead off the bracelet. Six wrong letters and the game is lost.
- **Clues** open as the wrong letters add up: the era's colour after 2 (named, and the whole
  screen takes it on), the year after 3, the album after 4 and the track number after 5.
- **When it's over**, it shows the song, album and year, the streak, and how long until
  the next one. A win gets sparkles.
- **Practice:** as many games as you like, from one era or any. It doesn't repeat the last
  30 songs or give away today's, and practice games don't count towards the streak.
- **Stats:** games played, the share won, the current streak and the best one, a chart of
  how many wrong letters each win took, and the last few days' songs.

Leaving the app, or the Pi losing power, doesn't lose a game half played. Everything is kept
in `/var/lib/pi3-touch-swifty/swifty.json`. A day counts towards the streak once it's
finished, won or lost; a day missed ends the streak.

## Songs

`songs.json` holds Taylor Swift's 12 studio albums, standard editions, with their
release years, track lists and a colour for each era. Titles are written as on the album,
without features or "(Taylor's Version)". Two are left out: *22* (no letters to guess)
and one with a swear in it. There's no network: no Taylor Swift data service exists, and
the albums don't change. When a new album comes out, add it at the end of `songs.json`
with its year, a colour for its era and its tracks, then deploy.

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
clues, the song of the day and the cycle), streaks, practice, and progress kept across
restarts. When pygame is installed, its screen tests also play by tapping, check that every
title fits its tiles, and draw every view.

On a Mac with pygame, `python3 apps/swifty/swifty_display.py` opens it in a window. You
can type letters as well as click them.
