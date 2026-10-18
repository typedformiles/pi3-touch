# Departures

Live Elizabeth line train times on the HyperPixel (480x800 portrait): the home station
in both directions, and the westbound trains from London with the time each one gets home.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Departures** from the menu (page 2: swipe left or tap the second dot).

## Screens

Tap a station along the top, or swipe left or right, to switch. Above the board is the
line's status (green dot for a good service, amber for reduced service or minor delays,
red for severe delays or closures) with the start of TfL's message. Tap it to read all of it.

| Station | Shows |
|---|---|
| **Taplow** | Two halves, To London and To Reading. Each starts with the next train in big print: its time, destination, platform, the minutes until it's due and the gap to the one after. The later trains are listed under it |
| **Paddington** | Westbound trains only. **Next train to Taplow** is at the top, with the time it gets to Taplow and the ride time. Then every westbound train, with its Taplow arrival under it. Trains that don't stop at Taplow (Heathrow) are greyed out |

How it knows a Paddington train's Taplow time: a train keeps the same id all along its
route, so the same id turning up in Taplow's predictions, going the same way, gives the
arrival time. TfL only predicts about an hour and a half ahead, so a later train's time
can't be found that way yet. It's estimated from the ride time of the trains that were
matched, and marked `~`. A train counts as stopping at Taplow if TfL has it there, or if
it's heading for Maidenhead, Twyford or Reading.

Times are the minutes until the train is due, then hours and minutes past the hour mark.
A train drops off 30 seconds after its time. The dot at the top right is green when the
times are under two minutes old, amber when they're older or the last fetch failed, red
when there are none yet. The Home and moon (sleep) buttons work as they do in the other
apps.

## Stations

`departures.json`:

```json
{"line": "elizabeth", "stations": [
  {"name": "Taplow", "stop": "910GTAPLOW", "sections": [
    {"title": "To London", "direction": "inbound"},
    {"title": "To Reading", "direction": "outbound"}]},
  {"name": "Paddington", "stop": "910GPADTLL", "direction": "outbound",
   "to": {"name": "Taplow", "via": ["Maidenhead", "Twyford", "Reading"], "minutes": 35}}
]}
```

- `stop`: TfL's id for the station. Find one with
  `https://api.tfl.gov.uk/StopPoint/Search/<name>?modes=elizabeth-line`. A big station can
  have several; Paddington's Elizabeth line platforms are `910GPADTLL`.
- `sections`: split the board by direction. TfL calls towards London and Abbey Wood/
  Shenfield `inbound`, and towards Reading and Heathrow `outbound`.
- `direction`: show one direction only.
- `to`: another station on the list. Each train gets its time there. `via` lists the
  destinations beyond it (a train heading there stops on the way). `minutes` is the usual
  ride, used until a train can be matched.

Deploy again after editing.

## Data

- **[TfL Unified API](https://api.tfl.gov.uk/)**: free; no key needed at this rate (TfL
  allows a few hundred calls a minute without one). The app fetches each station's
  predictions every 30 seconds (1-2 KB compressed) and the line's status every 2 minutes.
- Only Elizabeth line trains are in TfL's data. GWR trains need National Rail's own feed
  (Darwin), which needs a token.
- **No cache:** predictions are kept in memory only. After a restart, old times would be wrong.
- TfL sometimes lists a "ghost" train due right now with no platform. These are left out
  (a real prediction always expires a minute before its time; these hang around an hour).
- To check the stations against the live API (on the Pi, or on a Mac from the repo):
  `python3 /opt/pi3-touch/apps/departures/departures_data.py --check`

## Service

```bash
journalctl -u pi3-departures -f        # live logs ([departures] lines for fetch problems)
sudo systemctl restart pi3-departures
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side: names, direction filters,
ghosts, matching trains between stations, status. Its screen tests also draw both stations
when pygame is installed.
