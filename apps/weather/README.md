# Weather

Weather for a few saved places on the HyperPixel (480x800 portrait): conditions now,
wind for sailing, a 7-day forecast and, for coastal places, tides.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Weather** from the menu.

## Screens

Tap a place along the top to switch to it. Tap a page along the bottom, or swipe left or
right, to change page.

| Page | Shows |
|---|---|
| **Now** | Temperature and conditions; wind speed, gusts, direction (compass) and Beaufort force; the tide (rising or falling, and the next high or low) at the coast, or the chance of rain in the next 3 hours inland; sunrise and sunset |
| **Wind** | The next 12 hours: direction, speed and gusts in knots, as coloured bars |
| **Week** | 7 days: conditions, high and low temperature, strongest wind and gust, chance of rain |
| **Tides** | Only for places with a `tide_station`. Rising or falling and the height now; time to the next high and low; the day's tide curve and high and low table; the arrows step through 7 days |

Wind colours (knots): grey under 5 · green 5–11 · yellow 12–17 · orange 18–24 · red 25+.
Wind arrows point the way the wind is blowing, as on weather maps. The text gives the
direction it comes from.

The dot at the top right turns green when the weather was updated in the last hour,
amber when it's older (the app keeps showing what it has cached), and red when there's
no data yet. The Home and moon (sleep) buttons work as they do in Moode Remote.

## Places

Places are listed in `locations.json`:

```json
{"name": "Hunstanton", "lat": 52.938, "lon": 0.490, "tide_station": "Hunstanton"}
```

`tide_station` is an ADMIRALTY station name. The app looks the name up once and
remembers it. `cycle_seconds` (default 0, meaning off) makes the pages advance by
themselves after a minute without a touch. Deploy again after editing.

## Data

- **Weather:** [Open-Meteo](https://open-meteo.com/), free with no key (it uses the Met
  Office's UK model here). The app fetches it every 15 minutes for every place, so
  switching between places is instant.
- **Tides:** the [ADMIRALTY UK Tidal API](https://admiraltyapi.portal.azure-api.net/),
  *Discovery* tier, which is free with a key. It gives official high and low water times
  and heights (above chart datum, the same figures the BBC shows) for today and the next
  6 days. Between those points the curve is drawn as a cosine, the usual tide-table
  approximation. The app fetches tides every 6 hours. © Crown copyright, UKHO.
  - Save the key on the Pi with `bash mac/set-tide-key.sh`. Run it in your own Terminal,
    because the key is typed at a hidden prompt. It is stored in
    `/etc/pi3-touch/admiralty-key` and never goes into the repo.
  - To check the key and stations on the Pi:
    `python3 /opt/pi3-touch/apps/weather/weather_data.py --check`
- **Cache:** everything fetched is cached in `/var/lib/pi3-touch-weather`, so after a
  restart, or when you're offline, the screen shows the last data it had.

## Service

```bash
journalctl -u pi3-weather -f        # live logs ([weather] lines for fetch problems)
sudo systemctl restart pi3-weather
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side. Its screen tests also draw
every page when pygame is installed.
