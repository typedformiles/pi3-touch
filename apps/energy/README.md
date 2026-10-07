# Energy & Grid

How clean the electricity is on the HyperPixel (480x800 portrait): carbon intensity now,
where the power is coming from, and the greenest times in the next two days to run the
washing machine, dishwasher or car charger.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Energy & Grid** from the menu (page 2: swipe left or tap the second dot).

## Screens

Tap a place along the top to switch to it. Tap a page along the bottom, or swipe left or
right, to change page.

| Page | Shows |
|---|---|
| **Now** | Carbon intensity this half hour (gCO₂/kWh), its band on the five-step scale (Very low to Very high), and whether the next 3 hours look greener or dirtier; the greenest 3 hours in the next 24 and when they start; the generation mix as a bar with the zero-carbon share |
| **Mix** | The mix as a ring, split into zero carbon (wind, solar, hydro, nuclear), fossil (gas, coal) and other (biomass, imports); then one bar per fuel, biggest first |
| **Forecast** | The next 48 hours as half-hourly bars coloured by band, with each day's greenest 3 hours shaded; below, the greenest 3 hours for today, tomorrow and the day after, and the worst 3 hours of the next 24 (Avoid) |

Band colours: dark green Very low · green Low · yellow Moderate · orange High · red Very high.
The API sets each half hour's band (the limits come down a little every year). An average
gets the band of the half hour closest to it in value.

The figure is the forecast for the half hour unless the API has a measured value for it
yet. Measured values only exist for Great Britain, and arrive later. The dot at the top
right turns green when the data was updated in the last hour, amber when it's older (the app
keeps showing what it has cached), and red when there's no data yet. The Home and moon
(sleep) buttons work as they do in the other apps.

## Places

Places are listed in `places.json`:

```json
{"name": "Maidenhead", "postcode": "SL6"}
{"name": "Great Britain"}
```

A place with a `postcode` (the outward part only, e.g. `SL6`) shows its region (SL6 is
South England), whose mix and forecast differ a lot from the national ones. A place without
one shows Great Britain. `window_hours` (3) is how long the "greenest hours" are; set it to
your washing machine's cycle. `cycle_seconds` (15; 0 turns it off) makes the pages advance
by themselves once nobody has touched the screen for a minute. Deploy again after editing.

## Data

- **[Carbon Intensity API](https://carbonintensity.org.uk/)** from NESO (the GB electricity
  system operator) with Oxford University and WWF. It's free and needs no key. For a region,
  one call gives 48 hours of half-hourly intensity and mix. For Great Britain, the forecast
  comes with measured values where they exist, and a second call gives the mix now. The app
  fetches each place every 30 minutes.
- **Cache:** the last data for each place is kept in `/var/lib/pi3-touch-energy`, so after
  a restart, or when you're offline, the screen shows what it had (the forecast covers
  two days).
- To check the places against the live API (on the Pi, or on a Mac with `apps/energy/...`):
  `python3 /opt/pi3-touch/apps/energy/energy_data.py --check`

## Service

```bash
journalctl -u pi3-energy -f        # live logs ([energy] lines for fetch problems)
sudo systemctl restart pi3-energy
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side. Its screen tests also draw
every page when pygame is installed.
