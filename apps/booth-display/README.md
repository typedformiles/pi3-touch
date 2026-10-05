# Booth Display

Full-screen slides on an HDMI monitor, with Previous / Pause / Next controls on the
HyperPixel. Built for the Neuralift stand at G2E 2026 (Las Vegas) as a stand-in banner.

Part of [Pi3 Touch](../../README.md) - pick **Booth Display** from the menu.

## How it works

| Piece | Job |
|---|---|
| `showloop` | Runs mpv full screen on HDMI. Shows the images in `/boot/firmware/show` (the SD card's boot partition, so a Mac can edit it), or loops `~/videos/*.mp4` if there are none. |
| `rotate.lua` | mpv script: next slide 20 s after the current one is actually on screen (mpv's own timer rushed through slides after a slow one). Takes `next` / `prev` / `toggle` from the touch panel and records `playing` / `paused`. |
| `showtouch.py` | Reads the HyperPixel touch screen and draws the control panel straight into the framebuffer, because mpv owns the display hardware for HDMI. |
| `art/make_panels.py` | Renders the panel images (Mac, Pillow). The `.rgb.gz` files are what the Pi draws. |

## HyperPixel controls

| Area | Action |
|---|---|
| Top strip - **hold 1.5 s** | Back to the menu (a hold, so visitors can't exit by accident) |
| Upper third | Previous slide |
| Middle | Pause / resume (the band turns purple while paused) |
| Lower third | Next slide |

## Changing slides

Put 1920x1080 PNG/JPGs in a folder (default `~/Desktop/booth-show`), name them `1-…`,
`2-…` for the order, Mac on the same network/hotspot as the Pi, then:

```bash
bash mac/slides.sh [folder]
```

No network? Put the card in a Mac and replace the files in the `show` folder on the
`bootfs` drive - they're picked up on the next boot.

Slide time: `ROTATE=` near the top of `showloop`.

## Lessons from the show floor

- **Video went black after a frame or two** on the real 1080p monitor. mpv's zero-copy
  hardware decoding (`--hwdec=v4l2m2m`) hands frames straight to a display plane, and the
  Pi rejected them (`Failed to commit atomic request: Error number 22`). Benchmarks with no
  monitor attached looked perfect, because mpv counts frames it *sent*, not frames shown.
  Still images (`--vo=drm`, no GPU or decoder) were rock solid.
- Pi 3B: H.264 only, max 1080p30. `--hwdec=auto-safe` never picked the hardware decoder.
- Install mpv with `--no-install-recommends` on Lite - its recommends are ~190 MB.
- With no monitor attached at boot, the Pi only offers modes up to 1024x768
  (`video=HDMI-A-1:...D` on the kernel command line didn't change that). A udev rule
  restarts the slides when a monitor appears, so they pick up 1080p.
