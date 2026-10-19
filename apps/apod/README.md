# Space Picture

NASA's Astronomy Picture of the Day on the HyperPixel (480x800 portrait): a different
picture of the universe every day, with an astronomer's explanation, and the weeks before it.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`,
then pick **Space Picture** from the menu (page 3).

## Screens

Pages run along the bottom. On Picture and About, swipe left for the day before and right
for the day after.

| Page | Shows |
|---|---|
| **Picture** | The day's picture as big as fits, over a soft blur of itself so a landscape picture still fills the portrait screen. Tap it to fill the screen instead (cropped); tap again to fit. The title and credit sit at the foot. A video day shows NASA's still from it, marked "Still from a video". If there's no still, a card says to watch it at science.nasa.gov/apod |
| **About** | Title, full credit, and the explanation, as big as fits. ‹ › change day |
| **Days** | Every day kept, newest first, with a thumbnail. Tap one to see it; ‹ Newer and Older › page through |

Left alone until the screen dims, it goes back to today's picture, fitted. The dot at the
top right is green when all is well, amber when the last fetch failed (kept pictures still
show), and red before the first. The Home and moon (sleep) buttons work as they do in the
other apps.

## Data

- **[APOD](https://science.nasa.gov/apod/)** moved to science.nasa.gov in 2026. Since
  then, NASA's APOD API (api.nasa.gov) answers with the same placeholder for every day,
  with the NASA logo for a picture. This app reads the new site's RSS feed instead
  (`/feed/apod-basic/`, free, no key). It has the last 30 days, each with title,
  explanation, credit, a description and the full-size picture. The app checks it every
  3 hours; a new picture appears around 05:00 UK time.
- **Pictures:** NASA's image server resizes on request, so the app asks for a 960-pixel
  copy (about 50 KB as JPEG, while originals run to several MB) and a 120-pixel thumbnail.
  It fetches one at a time, newest first, every few seconds. Pictures that were PNGs stay
  PNG (up to ~1.3 MB).
- **Kept** in `/var/lib/pi3-touch-apod`: up to 45 days of entries and pictures (about
  8 MB), so it works offline and the Days page grows beyond the feed's 30.
- To check the feed (on the Pi, or on a Mac from the repo):
  `python3 /opt/pi3-touch/apps/apod/apod_data.py --check`

## Service

```bash
journalctl -u pi3-apod -f        # live logs ([apod] lines for fetch problems)
sudo systemctl restart pi3-apod
```

## Off-Pi

`python3 -m unittest discover tests` covers the data side: parsing the feed (videos,
stills, YouTube thumbnails, links that aren't videos), dates, downloads newest first,
error pages, keeping days. Its screen tests also draw every page and day, fitted and
filled, when pygame is installed.
