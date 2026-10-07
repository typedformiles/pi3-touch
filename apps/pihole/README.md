# Pi-hole

What the network's [Pi-hole](https://pi-hole.net/) is blocking, on the HyperPixel (480x800
portrait), with a button to pause blocking when it breaks a website.

Part of [Pi3 Touch](../../README.md). Install the whole project with `mac/deploy.sh`, save
the Pi-hole's app password (below), then pick **Pi-hole** from the menu (page 2).

## Screens

The strip under the clock shows whether blocking is on and has a **Pause** button: pick
30 seconds, 5 minutes, 30 minutes or an hour. The Pi-hole turns blocking back on by itself
when the time is up, and the strip counts down until then. **Resume** turns it back on early.
If blocking was turned off some other way, with no timer, the strip says *Blocking off* and
Resume turns it back on.

Tap a page along the bottom, or swipe left or right, to change page.

| Page | Shows |
|---|---|
| **Now** | Lookups in the last 24 hours, how many were blocked (and the share, as a ring), how many devices asked; the 24 hours as 10-minute bars, allowed and blocked; the blocklist's size and age, and lookups a second |
| **Top** | The most blocked domains, the most allowed domains, or the busiest devices (tap to switch) |
| **Live** | The latest lookups, updated every 3 seconds: blocked in red with a cross, answered from the cache in blue, the rest in green |

The dot at the top right is green while the Pi-hole answers, amber when it has stopped
answering (the screen keeps what it last had, and the reason shows next to the clock), and
red before the first answer. The Home and moon (sleep) buttons work as they do in the
other apps.

## Setup

- **App password:** in the Pi-hole's web page go to *Settings > Web interface / API* and
  choose *Configure app password*. Copy the password, then *Enable* it. On the Mac run
  `bash mac/set-pihole-key.sh` in your own Terminal. It asks for the password at a hidden
  prompt, saves it in `/etc/pi3-touch/pihole-password` (never in the repo) and checks it.
  An app password doesn't change the one you use to log in to the web page.
- **Address:** `url` in `pihole.json` (`http://10.0.0.37`). It's an IP address so that it
  doesn't depend on DNS, which is the thing the Pi-hole controls. If its address changes,
  edit it and deploy again.
- `pause_choices` lists the four pause lengths in seconds. `cycle_seconds` (0, off) makes
  the pages advance by themselves once nobody has touched the screen for a minute.

## How it talks to the Pi-hole

The Pi-hole v6 API (`/api`, documented on the Pi-hole itself at `http://10.0.0.37/api/docs`).
The app logs in once with the app password and uses that session for everything. A Pi-hole
only allows a few sessions at once, so the app logs out when it stops. The Now numbers are
fetched every 10 s, the top lists every minute, the 24-hour history every 5 minutes, and the
query log only while Live is showing. Pause and Resume use `POST /api/dns/blocking` with a
timer, the same as the Pi-hole's own *Disable blocking* menu.

Check the password and what the Pi-hole says, on the Pi:
`python3 /opt/pi3-touch/apps/pihole/pihole_data.py --check`

## Service

```bash
journalctl -u pi3-pihole -f        # live logs ([pihole] lines: problems, pauses)
sudo systemctl restart pi3-pihole
```

## Off-Pi

`python3 -m unittest discover tests` runs against a fake Pi-hole (`tests/test_pihole.py`).
Its screen tests also draw every page when pygame is installed.
