# Trivia

A quiz on the HyperPixel (480x800 portrait): rounds of ten questions, multiple choice or
true/false, in 24 topics and three difficulties. Good for passing round at quiz night.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Trivia** from the menu (page 3).

## Playing

- The first time, **pick a topic** (or Anything) and a difficulty (or Any), then Start.
  After that the app opens straight into a round of your last topic. Tap **Topics** any
  time to change.
- Each question shows its topic and difficulty, with the round's progress across the top
  (green right, red wrong). Tap an answer. It turns green if it's right, red if not, and
  the right one turns green either way. Then tap **Next**.
- After the tenth question, **Results** shows the score, a word on how it went, and
  every question with its right answer. **Play again** starts another round of the same topic.
- Questions don't come round again until a topic runs out. Then the app says so and
  offers to start them again.

The dot at the top right is green when questions are stocked for the topic, amber when
the last fetch failed (play carries on from the stock), and red when there are none yet.
The Home and moon (sleep) buttons work as they do in the other apps.

## Settings

`trivia.json`: `round` (questions per round, 10), and the `category` (an
[Open Trivia DB id](https://opentdb.com/api_category.php), 0 for anything) and
`difficulty` (`any`, `easy`, `medium` or `hard`) to start with before a topic has been
picked. Deploy again after editing.

## Data

- **[Open Trivia Database](https://opentdb.com/)**: free, no key, community-written
  questions under CC BY-SA 4.0. The API allows one call every 5 seconds. The app keeps a
  stock of at least 15 questions for the chosen topic, fetching 20 at a time in the
  background. So a round starts at once, and works offline once stocked.
- A **session token** stops questions repeating. It's forgotten by the API after 6
  hours unused, and the app then gets a new one.
- **Saved** in `/var/lib/pi3-touch-trivia/trivia.json`: the stock, the token and the
  last topic.
- Some questions are US-centric or dated; that's the database.
- To check the API (on the Pi, or on a Mac from the repo):
  `python3 /opt/pi3-touch/apps/trivia/trivia_data.py --check`

## Service

```bash
journalctl -u pi3-trivia -f        # live logs ([trivia] lines for fetch problems)
sudo systemctl restart pi3-trivia
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side: decoding, shuffling, the stock,
the 5-second rule, tokens, topics running out. Its screen tests play a whole round when
pygame is installed.
