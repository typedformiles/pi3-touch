# Moode Remote Display

A fullscreen remote for [Moode Audio](https://moodeaudio.org/), running on a Raspberry Pi
with a HyperPixel 4.0 touch screen. It covers what's playing, what's coming up and one-tap
mixes. It doesn't need a desktop environment, because it draws straight to the screen
through KMS/DRM.

![Python](https://img.shields.io/badge/python-3-blue)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-red)

---

## How it talks to Moode

The remote uses the **Moode web app's API on `moode.local:5005`** (`moode_api.py`). That
service runs on the Moode Pi in front of MPD and adds listener profiles, likes, Bliss mixes
and stations, one-tap quickplay, and a record of who queued each track. Every request
carries the current listener's profile id, so likes, mixes and station steering belong to
whoever is picked.

The remote checks the player's status once a second. It fetches the queue only when the
queue changes, and album art only when the song changes. It redraws the screen only when
something on it has changed, which is about once a second while music plays.

---

## Hardware

- **Display Pi**: Raspberry Pi 3 Model B (or newer)
- **Screen**: Pimoroni HyperPixel 4.0 Touch (rectangular, 480x800 portrait)
- **Moode Pi**: a separate Pi running Moode Audio **and the Moode web app on port 5005**

---

## Installation

This app is part of [Pi3 Touch](../../README.md). Install the whole project with
`mac/deploy.sh` (see the top-level README), then pick **Moode Remote** from the menu on the
HyperPixel. The menu remembers it and starts it automatically on the next boot.

---

## Screens

Tabs run along the bottom; swipe sideways to move between them.

**Now**: album art, the title, artist, album and year, a progress bar, and previous, play
and next buttons. Below them is a row of four buttons:

| Button | Does |
|---|---|
| ♥ Like | Adds the song to (or removes it from) the current listener's Likes |
| ✦ Bliss / Station | Opens a menu: start a station from this song, add 20 similar songs, play a mix now, more albums like this, turn auto-queue on or off, or take over someone else's station. When a station is on, this button is lit and shows whose station it is |
| ⇄ Shuffle | Turns shuffle on or off |
| ↻ Repeat | Cycles through repeat all, repeat this song, and off |

**Up Next**: the current song, then the queue. Each track shows the photo of whoever
queued it, or ✦ if Bliss picked it. The arrows page through the queue, and **Clear**
(tap twice) empties everything after the current song. Tap a track to play it now, play it
next, like it, queue more like it, or remove it.

**4U**: one tap to start something, and the screen switches to Now:
- the moods (Late night, Chill, Energetic)
- Shuffle my Likes and Mix from my Likes
- Random album and Random song
- by decade
- the six biggest genres

**Header**: the clock, then the current listener's photo (tap it to choose who's
listening, which the Pi remembers), Home (back to the Pi3 Touch menu), sleep, and a
connection dot.

The screen dims after 5 minutes without a touch and wakes again when the track changes.
The moon button blacks it out until you touch it.

---

## Configuration

| Setting | Default | Where |
|---|---|---|
| Moode web app | `http://moode.local:5005` | `MOODE_API` environment variable (`sudo systemctl edit pi3-moode-remote`) |
| Dimming | 5 min, brightness 120 | `DIM_AFTER`, `DIM_BRIGHTNESS` at the top of `moode_display.py` |
| Listener | the first profile | chosen on screen and saved in `/var/lib/pi3-touch-moode-remote/profile` |

---

## Service Management

```bash
sudo systemctl status pi3-moode-remote        # status
journalctl -u pi3-moode-remote -f             # live logs
sudo systemctl restart pi3-moode-remote       # after config changes
```

---

## Troubleshooting

**"Connecting to moode.local:5005…"**
The Moode web app isn't reachable. From the display Pi, run
`curl -s http://moode.local:5005/api/status`. If `moode.local` doesn't resolve on the
network, set `MOODE_API` to the Moode Pi's IP address.

**Wrong DRI device**
The HyperPixel may appear as card0 or card1. Check with `ls /dev/dri/`, then run
`sudo systemctl edit pi3-moode-remote` and add `Environment=SDL_KMSDRM_DEVICE_INDEX=1`.

**HyperPixel not detected**
Run `grep hyperpixel /boot/firmware/config.txt`. It should show
`dtoverlay=vc4-kms-dpi-hyperpixel4`.

**No album art**
Art comes from the web app's `/api/art`. Look for `[art]` lines in
`journalctl -u pi3-moode-remote -f`.

---

## Dependencies

Installed by `pi/install.sh` from apt (no pip): `python3-pygame`, `python3-pil`, `libgbm1`,
`libegl1`. The API client uses only the Python standard library.

---

## License

[MIT](../../LICENSE)
