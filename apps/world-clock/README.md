# World Clock

Six cities as analogue clocks on the HyperPixel (480x800 portrait): San Francisco,
New York, London, Dubai, Tokyo and Sydney.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **World Clock** from the menu.

## The faces

Each face is coloured by the sky in that city right now, worked out from where the sun
is there (`sky.py`):

| Sun | Face |
|---|---|
| well below the horizon | deep navy |
| twilight | violet, then rose |
| around sunrise and sunset | gold |
| up | morning blue, then day blue |

Hands are dark on light faces and light on dark faces, and the second hand is orange. A
small sun or moon below the centre shows whether it's day there. Under each clock is the
digital time, "tomorrow" or "yesterday" when the date differs from London's, and the
hours ahead of or behind London. These follow daylight-saving changes automatically.

The header shows today's date in London. The Home and moon (sleep) buttons work as they
do in the other apps, and the screen dims after 5 minutes without a touch.

## City pages

Tap a clock to open that city. Tap a page along the bottom, or swipe left or right, to
change page. After 20 seconds without a touch the pages advance every 15 seconds by
themselves. **‹ Cities**, or 3 minutes without a touch, goes back to the clocks.

| Page | Shows | From |
|---|---|---|
| **Now** | The local time on that city's sky colour with its clock; the weather now; sunrise, sunset and daylight; tonight's moon (mirrored for Sydney); whether it's a good time to call (daytime, 08:00–22:00, in both places) and the best window; an earthquake of M3.5+ within 300 km in the last day, if there was one | Open-Meteo, USGS, calculated on the Pi |
| **Weather** | Now; the next 12 hours (every 2 hours); 5 days; air quality (US AQI), today's UV, and sea temperature with wave height for coastal cities | Open-Meteo forecast, air-quality and marine APIs |
| **News** | That city's latest headlines (text only) | KQED, New York Times, BBC London, The National, Japan Times, Sydney Morning Herald |
| **Money** | £1 in the local currency, and the reverse; whether the stock exchange (NYSE, LSE, DFM, TSE, ASX) is open, with a countdown; the next public holidays | Frankfurter (European Central Bank rates), trading hours, Nager.Date |

Some caveats:
- Exchange status comes from regular trading hours on weekdays. Exchange holidays don't
  match public holidays, so on a public holiday the page says so rather than guessing.
- There is no free source of UAE holiday dates, so Dubai shows its weekend instead.
- The dirham is pegged to the dollar, so £1 in dirhams is worked out through the dollar.

All the sources are free and need no key. Only the city on screen is fetched, and only
what has gone out of date (weather every 15 minutes, news every 30, exchange rates every
6 hours, holidays daily). Everything is cached in `/var/lib/pi3-touch-world-clock`, so a
city opens instantly and still shows what it last had when you're offline. On the clocks,
nothing is fetched at all.

## Changing the cities

Cities are listed in `cities.py`, in screen order. Each one has:
- a name and [time zone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)
- a latitude and longitude, used for the sky colour and weather
- an offshore point for sea temperature
- its currency
- a holiday country and region
- its stock exchange and trading hours
- a news feed

Six fit the screen. Deploy again after editing.

## Service

```bash
journalctl -u pi3-world-clock -f
sudo systemctl restart pi3-world-clock
```

The clocks are redrawn once a second, for the second hands. Everything except the hands
is cached and rebuilt once a minute, which keeps it at about 2% of a Pi 3 core. City
pages redraw only when the minute ticks over, the page changes or new data arrives.
