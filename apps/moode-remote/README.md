# Moode Remote Display

A fullscreen "now playing" companion display for [Moode Audio](https://moodeaudio.org/).
Runs on a Raspberry Pi with a HyperPixel 4.0 touch screen, connecting to Moode/MPD
over your local network. No desktop environment needed — renders directly via KMS/DRM.

![Python](https://img.shields.io/badge/python-3-blue)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-red)

---

## Hardware

- **Display Pi**: Raspberry Pi 3 Model B (or newer)
- **Screen**: Pimoroni HyperPixel 4.0 Touch (rectangular, 480x800 portrait)
- **Moode Pi**: separate Pi running [Moode Audio](https://moodeaudio.org/) (any model)

The display Pi connects to Moode over the network — they don't need to be the same device.

---

## Installation

This app is part of [Pi3 Touch](../../README.md) - install the whole project with
`mac/deploy.sh` (see the top-level README), then pick **Moode Remote** from the
menu on the HyperPixel. The menu remembers it and starts it automatically next boot.

---

## Configuration

Edit the `# Config` section at the top of `moode_display.py`:

| Setting           | Default              | Description                          |
|-------------------|----------------------|--------------------------------------|
| `MPD_HOST`        | `moode.local`        | Hostname/IP of your Moode Pi         |
| `MPD_PORT`        | `6600`               | MPD port (default is always 6600)    |
| `MOODE_URL`       | `http://moode.local` | Base URL for album art fallback      |
| `SCREEN_W/H`      | `480` / `800`        | Screen resolution (match your display) |
| `DIM_AFTER`       | `300`                | Seconds of inactivity before dimming |
| `DIM_BRIGHTNESS`  | `120`                | Brightness when dimmed (0-255)       |

---

## Layout (portrait 480x800)

```
+---------------------------+
|  12:34          ^  )  *   |  <- Clock, Home, sleep, connection dot
|                           |
|  +-------360px----------+ |
|  |                       | |
|  |                       | |
|  |      Album Art        | |  <- 360x360px (75% width)
|  |                       | |
|  |                       | |
|  +-----------------------+ |
|  Track Title               |
|  Artist Name               |
|  Album Name   > PLAYING    |
|                            |
|  ========------  2:14      |  <- Progress bar
|  1:30            4:02      |
|                            |
|    |<      >      >|      |  <- Touch controls
|                            |
|  -  ====----  Vol 65%  +  |  <- Volume
+----------------------------+
```

---

## Touch Controls

| Area               | Action                 |
|--------------------|------------------------|
| Prev button        | Previous track         |
| Play button        | Play / Pause           |
| Next button        | Next track             |
| - button           | Volume down (5% steps) |
| + button           | Volume up (5% steps)   |
| Home (house) icon  | Back to the Pi3 Touch menu |
| Moon icon          | Sleep (screen near-black) |
| Any touch          | Wake screen from dim   |
| Track/state change | Auto-wake from dim     |

---

## Service Management

```bash
sudo systemctl status pi3-moode-remote        # status
journalctl -u pi3-moode-remote -f             # live logs
sudo systemctl restart pi3-moode-remote       # after config changes
```

---

## Troubleshooting

**Black screen / app not starting**
```bash
journalctl -u pi3-moode-remote -f
```

**Wrong DRI device**
The HyperPixel may appear as card0 or card1. Check with:
```bash
ls /dev/dri/
```
Then override the device index:
```bash
sudo systemctl edit pi3-moode-remote
# Add: Environment=SDL_KMSDRM_DEVICE_INDEX=1
```

**Can't connect to MPD**
On the Moode Pi, check `/etc/mpd.conf` — `bind_to_address` must be `any` or absent.
Test from display Pi: `nc -zv moode.local 6600`

**HyperPixel not detected**
```bash
grep hyperpixel /boot/firmware/config.txt
# Should show: dtoverlay=vc4-kms-dpi-hyperpixel4
```

**Album art not showing**
Art is fetched via MPD `readpicture`/`albumart` (embedded tags), with Moode's
`coverart.php` as a fallback. Check `journalctl -u pi3-moode-remote -f` for
`[art]` log lines to see which method is being used or failing.

---

## Dependencies

Installed by `pi/install.sh` from apt (no pip): `python3-pygame`, `python3-pil`,
`python3-mpd` (the python-mpd2 library), `libgbm1`, `libegl1`.

---

## License

[MIT](../../LICENSE)
