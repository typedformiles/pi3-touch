# Planes Overhead

The aircraft around home on the HyperPixel (480x800 portrait): a radar-style map of the
sky, and for the aircraft you pick, its flight, route, type, height, speed and a photo.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Planes Overhead** from the menu (page 3).

## Screens

Pages run along the bottom: tap one or swipe left or right.

| Page | Shows |
|---|---|
| **Radar** | The sky around home (the blue dot) out to 5, 10 or 20 nm (the buttons bottom right; rings at half and full range). Each aircraft points the way it's flying, coloured by height (orange low, through yellow, green, cyan and blue, to violet above 30,000 ft), with its last 3 minutes of track. The nearest eight are labelled with callsign and height in hundreds of feet. Towns and Heathrow's runways are marked. Under the map, a card for one aircraft: callsign, airline, route, type and registration, height (with ↑ climbing or ↓ descending), ground speed, how far away and which way, and a photo when there is one. It's the nearest aircraft unless you tap another; tap empty sky to go back to the nearest. "Overhead now" is shown within 1.5 nm and below 10,000 ft. An aircraft squawking an emergency (7500, 7600 or 7700) turns red |
| **List** | Every aircraft in range, nearest first: callsign, route (airport codes), type, height and distance. Tap one to see it on the radar |

Aircraft on the ground are left out (`show_ground` to include them). Heights are above
sea level: transponders report height against standard pressure (1013 hPa), so below
6,000 ft (the UK transition altitude) it's corrected with the pressure setting the
aircraft around have set. On a high-pressure day that's a few hundred feet. Above, it's
the flight level, as everyone uses.

The dot at the top right is green when the positions are fresh, amber when they're over a
minute and a half old or the last fetch failed, red before the first. The Home and moon
(sleep) buttons work as they do in the other apps.

## Settings

`planes.json`:

- `home`: the centre of the map (`name`, `lat`, `lon`). It's Taplow station; set it to your
  own spot for "overhead" to mean your roof.
- `ranges_nm` and `range_nm`: the zoom buttons and the one to start on (up to 25 nm).
- `show_ground`: include aircraft on the ground.
- `landmarks` (towns: `name`, `lat`, `lon`) and `runways` (two `ends`, an optional `name`
  and `label` position) to draw on the map.

Deploy again after editing.

## Data

- **Positions:** [adsb.fi](https://adsb.fi/)'s open data API, a community network of
  ADS-B receivers. It's free with no key, and allows one call a second. The app asks every 5 seconds
  while someone's using it and every 30 seconds once the screen has dimmed. If adsb.fi
  fails it uses [adsb.lol](https://adsb.lol/), which allows 500 calls a day, so the app
  stops at 400. Each reply covers 25 nm, about 5 KB compressed.
- **Routes, airlines, aircraft and photos:** [adsbdb.com](https://www.adsbdb.com/), free. The
  app looks up two aircraft every 5 seconds, nearest first. Routes are cached for a day and
  aircraft for a month in `/var/lib/pi3-touch-planes`. Routes are looked up by callsign
  and are usually right, but airlines reuse callsigns, so now and then one is stale. Photos
  (airport-data.com thumbnails) are fetched only for the aircraft on the card, and kept in memory.
- **On a Pi 3:** connections to each service are kept open and reused, because a fresh secure
  connection for every call cost ten times more CPU than the app's drawing.
- Military and some private aircraft don't broadcast their position, or hide their identity.
- To check what's overhead now (on the Pi, or on a Mac from the repo):
  `python3 /opt/pi3-touch/apps/planes/planes_data.py --check`

## Service

```bash
journalctl -u pi3-planes -f        # live logs ([planes] lines for fetch problems)
sudo systemctl restart pi3-planes
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side: distances and bearings, the
pressure correction, the fallback and its daily allowance, lookups and caching, trails.
Its screen tests also draw every page and range when pygame is installed.
