# Moode Remote Display

A lightweight fullscreen display for Pi 3B + HyperPixel 4.0 (480x800 portrait).
Connects to Moode/MPD running on another Pi over your local network.

---

## Hardware

- Raspberry Pi 3 Model B
- Pimoroni HyperPixel 4.0 Touch (rectangular, 480x800)

---

## OS

**Raspberry Pi OS Lite (32-bit, Bookworm)**
- Flash with Raspberry Pi Imager
- In OS Customisation (gear icon), set:
  - Hostname: `mooderemote`
  - Username: `tim`
  - WiFi credentials
  - Enable SSH

---

## Installation

```bash
# SSH into the Pi 3B
ssh tim@mooderemote.local

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
| `DIM_AFTER`       | `300`          | Seconds of inactivity before dim   |
| `DIM_BRIGHTNESS`  | `120`          | Screen brightness when dimmed (0-255) |

---

## Layout (portrait 480x800)

```
+-------------------------+
|  12:34              *   |  <- Clock + connection dot
|                         |
|  +---------360px------+ |
|  |                     | |
|  |                     | |
|  |     Album Art       | |  <- 360x360px (75% width)
|  |                     | |
|  |                     | |
|  +---------------------+ |
|  Track Title              |
|  Artist Name              |
|  Album Name   > PLAYING   |
|                           |
|  ========-----  2:14      |  <- Progress bar
|  1:30            4:02      |
|                           |
|    |<     >      >|       |  <- Touch controls
|                           |
|  -  ====----  Vol 65%  +  |  <- Volume
+---------------------------+
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
| Track/state change | Auto-wake from dim |

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

**Wrong DRI device**
The HyperPixel may appear as card0 or card1. Check with:
```bash
ls /dev/dri/
```
Then update `SDL_KMSDRM_DEVICE_INDEX` in `/etc/systemd/system/moode-display.service`.

**Can't connect to MPD**
On the Moode Pi, check `/etc/mpd.conf` — `bind_to_address` must be `any` or absent.
Test from Pi 3B: `nc -zv moode.local 6600`

**HyperPixel not working**
```bash
# Check config.txt (Bookworm uses /boot/firmware/config.txt)
grep hyperpixel /boot/firmware/config.txt
```

**Album art not showing**
Art is fetched via MPD `readpicture`/`albumart` (embedded tags), with Moode's
`coverart.php` as a fallback. Check `journalctl -u moode-display -f` for
`[art]` log lines to see which method is being used or failing.

---

## Dependencies

```
python3-pygame
python3-pil       (Pillow)
python3-mpd       (python-mpd2 library)
python3-requests
libegl-dev        (for KMS/DRM rendering)
libgbm1
```

All installable via apt, no pip required.
