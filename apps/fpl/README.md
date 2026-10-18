# FPL Live

A Fantasy Premier League team on the HyperPixel (480x800 portrait): this gameweek's points
as they come in, the team's mini-league tables, and what's coming up before the next
deadline.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **FPL Live** from the menu (page 2: swipe left or tap the second dot).

## Screens

Tap a page along the bottom, or swipe left or right, to change page.

| Page | Shows |
|---|---|
| **Live** | The gameweek's points so far, the average and highest score, and the overall rank (the arrow compares it with last gameweek's). The starting XI on a pitch: each player's points (captain doubled, bonus included), his game (`NFO 16:30` still to play, `nfo 67'` on now, `IPS FT` over; capitals for home, lower case away), C and V for captain and vice. Underneath is the bench, in order |
| **Leagues** | The team's mini-league tables, one league at a time (tap a league along the top): rank, move since last gameweek, team and manager, gameweek points and total. The team's own row is highlighted. A league too long to fit shows the top three and the rows around the team |
| **Next** | The next deadline and the time left (amber within a day, red within 6 hours), bank and team value; squad players with an injury or doubt flag (FPL's chance of playing) and this week's price changes; the squad's next five gameweeks of fixtures, coloured by FPL's difficulty rating (two in a cell is a double gameweek, an empty cell a blank) |

While a game is on:

- The points plate turns green for players on the pitch now.
- **Bonus** that the match would give if it ended now (the top three on BPS) is shown as
  `(3b)` and counted. Once FPL confirms the bonus (soon after the final whistle), it's
  included in the points.
- **Auto-subs** are done as FPL will do them at the end of the gameweek. A starter whose games are all
  over without him playing a minute is swapped for the first bench player (in bench
  order) who has played and keeps the formation legal. The swapped players are marked
  IN and OUT. If the captain doesn't play, the vice gets his multiplier. FPL's own site
  only does this when the gameweek ends, so the total here can be higher than FPL's
  until then.
- A player whose points go up (a goal, an assist, a clean sheet) gets a gold `+n` for 3 minutes.

The dot at the top right is green when all is well, amber when the last fetch failed (the
app keeps showing what it has cached), and red when there's no data yet. The Home and moon
(sleep) buttons work as they do in the other apps.

## Settings

`fpl.json`:

```json
{"team_id": 4192362, "leagues": [], "cycle_seconds": 0}
```

- `team_id`: the number in your FPL points page's address,
  `fantasy.premierleague.com/entry/<team_id>/event/7`.
- `leagues`: league ids to show, in order, at most 4 (the id is in the league's address).
  Empty shows the team's private leagues, in FPL's order. FPL's public leagues (Overall,
  country, club) have millions of teams, so they're left out unless listed.
- `cycle_seconds`: 0 keeps the page you chose. A number makes the pages advance by
  themselves after a minute untouched.

Deploy again after editing.

## Data

- **[FPL's API](https://fantasy.premierleague.com/api/bootstrap-static/)**: public, no key
  or login, read-only. Nothing here can change the team. It isn't documented and
  FPL changes it now and then. When that happens, `--check` (below) shows what broke.
- **How often:** the app follows the matches. Live points come every minute while a game is on, every
  5 minutes while bonus points are due, and at the next kick-off otherwise. League tables
  come every 5 minutes on match days and hourly otherwise (FPL updates its own tables a few
  minutes behind). The game's player list (1.8 MB, 170 KB compressed) comes every 2 hours,
  and after each deadline. Only the parts the screens use are kept.
- **Cache:** in `/var/lib/pi3-touch-fpl`, so after a restart, or offline, the screen shows
  what it had.
- To check the team against the live API (on the Pi, or on a Mac from the repo):
  `python3 /opt/pi3-touch/apps/fpl/fpl_data.py --check`

## Service

```bash
journalctl -u pi3-fpl -f        # live logs ([fpl] lines for fetch problems)
sudo systemctl restart pi3-fpl
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side: bonus, auto-subs, captaincy,
blanks and doubles, and when to fetch. Its screen tests also draw every page when pygame
is installed.
