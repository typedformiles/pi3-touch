#!/bin/bash
# Usage: bash mac/slides.sh [folder]      (default: ~/Desktop/booth-show)
# Replaces the Booth Display slides on the Pi with the PNG/JPGs in the folder.
# Slides rotate in filename order (name them 1-..., 2-... to set the order); 1920x1080 is best.
# Talks to the Pi directly over the local network/hotspot, so it uses no mobile data.
SRC=${1:-$HOME/Desktop/booth-show}
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}
SSH="ssh -i $KEY -o ConnectTimeout=15 tim@$PI"

shopt -s nullglob nocaseglob
files=()
for f in "$SRC"/*.png "$SRC"/*.jpg "$SRC"/*.jpeg; do
  case "$(basename "$f")" in ._*) continue;; esac
  files+=("$f")
done
[ ${#files[@]} -gt 0 ] || { echo "No .png/.jpg images found in $SRC"; exit 1; }
echo "Sending ${#files[@]} slide(s):"; printf '  %s\n' "${files[@]##*/}"

# Flatten onto black as plain 1920x1080 RGB PNGs - lighter for the Pi to put on screen.
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
for f in "${files[@]}"; do
  ffmpeg -v error -y -f lavfi -i color=black:s=1920x1080 -i "$f" \
    -filter_complex "[1]scale=1920:1080:force_original_aspect_ratio=decrease[i];[0][i]overlay=(W-w)/2:(H-h)/2,format=rgb24" \
    -frames:v 1 "$TMP/$(basename "${f%.*}").png" || { echo "Couldn't convert $f"; exit 1; }
done

# One connection: ship the slides, swap them in, restart the slideshow if it's running.
COPYFILE_DISABLE=1 tar -C "$TMP" -cf - . | $SSH '
  rm -rf ~/slides-upload && mkdir ~/slides-upload && tar -xf - -C ~/slides-upload &&
  sudo sh -c "rm -f /boot/firmware/show/* && cp ~tim/slides-upload/*.png /boot/firmware/show/" &&
  sudo systemctl try-restart pi3-booth-show.service &&
  echo && echo "On the Pi now:" && ls -1 /boot/firmware/show | sed "s/^/  /" &&
  echo "  booth display: $(systemctl is-active pi3-booth-show.service)"' || { echo "Couldn't reach the Pi - try: bash mac/pi-find.sh"; exit 1; }
