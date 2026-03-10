# Moode Remote Display

A lightweight fullscreen display for Pi 3B + HyperPixel 4.0 (480x800 portrait).
Connects to Moode/MPD running on another Pi over your local network.

---

## Hardware

- Raspberry Pi 3 Model B
- Pimoroni HyperPixel 4.0 Touch (rectangular, 480x800)

---

## OS

**Raspberry Pi OS Lite (32-bit, Bullseye)**
- Download from: https://www.raspberrypi.com/software/
- Flash with Raspberry Pi Imager
- Enable SSH in Imager advanced options
- Set hostname to `hifiremote`
- Set username to `tim`
- Set WiFi credentials in Imager

---

## Installation

```bash
# SSH into the Pi 3B
ssh tim@hifiremote.local

# Clone or copy files
git clone <your-repo> moode_display   # or scp the files across
cd moode_display

# Run setup
bash setup.sh

# Reboot (required for HyperPixel driver)
sudo reboot
```

After reboot the display app starts automatically via systemd.

---

## Configuration

Edit the `# Config` section at the top of `moode_display.py`:

| Setting           | Default        | Description                        |
|-------------------|----------------|------------------------------------|
| `MPD_HOST`        | `moode.local`  | Hostname of your Moode Pi          |
| `MPD_PORT`        | `6600`         | MPD port (default is always 6600)  |
| `MOODE_URL`       | `http://moode.local` | Base URL for album art fetching |
| `DIM_AFTER`       | `60`           | Seconds of inactivity before dim   |
| `DIM_BRIGHTNESS`  | `30`           | Screen brightness when dimmed (0-255) |

---

## Layout (portrait 480x800)

```
+-------------------------+
|  12:34              *   |  <- Clock + connection dot
|                         |
|    +---------------+    |
|    |               |    |
|    |   Album Art   |    |  <- 180x180px
|    |               |    |
|    +---------------+    |
|                         |
|  Track Title            |
|  Artist Name            |
|  Album Name             |
|  > PLAYING              |
|                         |
|  ========-----  2:14    |  <- Progress bar
|  1:30            4:02   |
|                         |
|    |<     >      >|     |  <- Touch controls
|                         |
|  -  ====----  Vol 65%  +|  <- Volume
+-------------------------+
```

---

## Touch Controls

| Area        | Action                    |
|-------------|---------------------------|
| Prev button | Previous track            |
| Play button | Play / Pause              |
| Next button | Next track                |
| - button    | Volume down (5% steps)    |
| + button    | Volume up (5% steps)      |
| Any touch   | Wake screen from dim      |

---

## Service Management

```bash
# Check status
sudo systemctl status moode-display

# View live logs
journalctl -u moode-display -f

# Restart
sudo systemctl restart moode-display

# Stop
sudo systemctl stop moode-display
```

---

## Troubleshooting

**Black screen / app not starting**
```bash
journalctl -u moode-display -f
```

**Can't connect to MPD**
On the Moode Pi, check `/etc/mpd.conf` — `bind_to_address` must be `any` or absent.
Test from Pi 3B: `nc -zv moode.local 6600`

**HyperPixel not working**
```bash
# Check driver is installed
ls /boot/overlays/hyperpixel4.dtbo

# Check config.txt (Bullseye uses /boot/config.txt)
grep hyperpixel /boot/config.txt
```

**Album art not showing**
Moode serves art at `http://moode.local/coverart.php` — test this in a browser.
If your library uses folder.jpg/cover.jpg files, Moode should serve these automatically.

---

## Dependencies

```
python3-pygame
python3-pil       (Pillow)
python3-mpd2
python3-requests
```

All installable via apt, no pip required.
