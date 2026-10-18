# Word of the Day

Merriam-Webster's word of the day on the HyperPixel (480x800 portrait), set like a
dictionary entry: the word, how to say it, what it means with an example, where it comes
from, and the words before it.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Word of the Day** from the menu (page 3).

## Screens

Pages run along the bottom. On Word and Story, swipe left (or tap ‹) for the day before
and right (or ›) for the day after.

| Page | Shows |
|---|---|
| **Word** | The day ("Today", "Yesterday", "3 days ago"), the word as big as fits, its pronunciation and part of speech, then each meaning followed by its example sentence (in italics, with a bar). At the foot, similar words, if the dictionary has any |
| **Story** | "Did you know?": where the word comes from, or how it's used. Then "In the news", a recent quote using it, with its source |
| **Past** | Every word kept, newest first, with a short meaning. Tap one to read it; ‹ Newer and Older › page through |

Left alone until the screen dims, it goes back to today's word on the Word page. The text
is set in FreeSerif, with italics where Merriam-Webster has them, and falls back to
DejaVu Serif without italics if FreeSerif isn't installed. Text shrinks to fit the screen
for long entries. The dot at the top right is green when the words are up to date, amber
when the last fetch failed (the kept words still show), and red before the first. The
Home and moon (sleep) buttons work as they do in the other apps.

## Data

- **[Merriam-Webster's Word of the Day](https://www.merriam-webster.com/word-of-the-day)
  RSS feed** (`/wotd/feed/rss2`): free, no key. A new word every day at 01:00 New York
  time (06:00 in the UK). The feed holds the last ten days, and the app checks it every
  3 hours.
- **Similar words:** the [Free Dictionary API](https://dictionaryapi.dev/) (free, no key),
  one word at a time. Phrases and rare words often have none. If a lookup fails, it's
  tried again an hour later, at most three times.
- **Kept** in `/var/lib/pi3-touch-wotd/words.json`: every word seen, for 60 days, so
  the Past page grows beyond the feed's ten, and everything works offline.
- To check the feed (on the Pi, or on a Mac from the repo):
  `python3 /opt/pi3-touch/apps/wotd/wotd_data.py --check`

## Service

```bash
journalctl -u pi3-wotd -f        # live logs ([wotd] lines for fetch problems)
sudo systemctl restart pi3-wotd
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side: parsing the feed (italics,
examples, several senses), building up the words kept, similar words and their retries.
Its screen tests also draw every page and step through the days when pygame is installed.
