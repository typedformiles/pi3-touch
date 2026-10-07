# Pi3 Touch

A Raspberry Pi 3 with a Pimoroni HyperPixel 4.0 touch screen that boots into a menu of
apps. Pick one and it runs; each app has a way back to the menu. After a power cut it
counts down and restarts whatever was running last.

| App | What it does |
|---|---|
| [Booth Display](apps/booth-display/) | Slides full screen on an HDMI monitor; the HyperPixel becomes a Previous / Pause / Next remote. |
| [Moode Remote](apps/moode-remote/) | "Now playing" screen and controls for a [Moode Audio](https://moodeaudio.org/) player on the network. |
| [Weather](apps/weather/) | Conditions, wind for sailing, 7-day forecast and - at the coast - tides, for a few saved places. |
| [World Clock](apps/world-clock/) | Six cities as analogue clocks, each face coloured by that city's sky right now. |
| [Energy & Grid](apps/energy/) | How clean the electricity is now, the generation mix, and the greenest hours of the next two days to plug in. |

## Layout

```
launcher/       the boot menu (launcher.py, apps.json, art/)
apps/<app>/     one folder per app
common/         pitouch.py - shared touch input + framebuffer drawing (stdlib only)
pi/             install.sh, systemd units, udev rule - runs on the Pi
mac/            tools you run on the Mac: deploy, find the Pi, send slides, status
tests/          off-Pi tests (python3 -m unittest discover tests; lua tests/test_rotate.lua)
```

## How it works on the Pi

- Everything installs to `/opt/pi3-touch`.
- `pi3-launcher.service` starts at boot and draws the menu: the apps as tiles, 2 x 2 per
  page (swipe sideways or tap the dots for more pages). Choosing an app starts its
  systemd target (`pi3-app-<id>.target`), which conflicts with the launcher and with every
  other app's target (the installer writes those) - so exactly one thing owns the screens
  at a time, however an app was started. That matters: only one program can drive the
  Pi's display hardware, which is also why HyperPixel panels are drawn straight into
  the framebuffer (`/dev/fb0`) instead of through a graphics library.
- An app asks for the menu by creating `/run/pi3-touch/request-home`;
  `pi3-home.path` notices and starts the launcher.
- The last app is remembered in `/var/lib/pi3-touch/last-app`; the menu counts down
  10 s and starts it again (showing the page it's on). Tap a tile to choose, anywhere else
  to stay on the menu.
- An app that keeps crashing falls back to the menu rather than leaving a dead screen.

## Setting up a Pi

1. **Flash** Raspberry Pi OS Lite (32-bit) with Raspberry Pi Imager. In the settings:
   hostname `mooderemote`, user `tim`, SSH on, your Wi-Fi.
   (Phone hotspot? The Pi 3B is 2.4 GHz only - on an iPhone turn on *Maximise Compatibility*,
   and avoid curly apostrophes in the network name.)
2. **SSH key** (once, from the Mac):
   `ssh-keygen -t ed25519 -f ~/.ssh/mooderemote_ed25519 -N ""` then
   `ssh-copy-id -i ~/.ssh/mooderemote_ed25519.pub tim@mooderemote.local`
3. **Deploy**: `bash mac/deploy.sh` - installs packages (roughly 150 MB, mostly mpv and
   pygame), enables the HyperPixel, installs the launcher and apps, reboots if needed.
   Re-run it any time to update; it's safe to repeat.

Name not resolving (common on hotspots)? `bash mac/pi-find.sh` finds the Pi's address;
then prefix any tool with it, e.g. `PI=172.20.10.3 bash mac/deploy.sh`.

## Day to day

```bash
bash mac/status.sh            # what's running, recent logs, power
bash mac/slides.sh [folder]   # replace the Booth Display slides
bash mac/add-wifi.sh <SSID>   # save a Wi-Fi network on the Pi
bash mac/set-tide-key.sh      # save the ADMIRALTY tide API key on the Pi (Weather's tides)
bash mac/demo-tour.sh [delay] # hands-free ~80 s tour of the apps for filming (taps injected on the Pi)
```

## Wi-Fi per app

The menu (and any app without its own setting) uses the top-level `"wifi"` network in
`launcher/apps.json`; an app can name its own (Booth Display uses the phone hotspot, so
`mac/slides.sh` can reach it at a show). The launcher switches with `pi/bin/pi3-wifi`, and
only when the network is in range and saved on the Pi - otherwise it stays put.

- Save a network (password typed at a hidden prompt, sent straight to the Pi):
  `bash mac/add-wifi.sh <SSID> [priority]` - run it in your own Terminal.
- The Mac has to be on whichever network the Pi is on to manage it.
- Wi-Fi region: channels 12-13 (common on UK routers) need `cfg80211.ieee80211_regdom=GB`
  in `/boot/firmware/cmdline.txt`; a US region can see those networks but can't join them.

## Adding an app

1. `apps/<id>/` with the app.
2. `pi/systemd/pi3-app-<id>.target` (`Wants=` its services, `Conflicts=pi3-launcher.service`)
   and its services (`PartOf=` the target) - copy the Moode Remote ones.
3. An entry in `launcher/apps.json`, then `python3 launcher/art/make_art.py` (Mac, Pillow) to
   redraw the menu pages - add a tile icon for the app id in `make_art.py` (without one it
   gets its initial).
4. Give the app a way to create `/run/pi3-touch/request-home`.
5. pygame apps: start only what you use (`pygame.display.init()` and `pygame.font.init()`,
   not `pygame.init()`, which also starts audio threads) and redraw only when something on
   screen changes - a Pi 3 repainting at 30 FPS spends over half a core on it.

## Hardware notes

- HyperPixel 4.0 rectangular: `dtoverlay=vc4-kms-dpi-hyperpixel4` (added by the installer).
- Its Goodix touch controller advertises landscape ranges (x 0-799, y 0-479), but raw x
  runs left-right and raw y top-bottom on the portrait panel - normalise each by its own
  range. (Assuming the long axis was vertical sent every tap to the middle button.)
- Mounted upside down? Set `"display": {"rotate": 180}` in `launcher/apps.json` (0 for upright)
  and deploy. Everything drawn on the HyperPixel turns over, and touches with it: the
  framebuffer and touch reading in `common/pitouch.py`, and the pygame apps through
  `common/pgscreen.py`. The HDMI output isn't affected.

## License

[MIT](LICENSE)
