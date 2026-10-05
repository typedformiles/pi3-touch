#!/bin/bash
# Usage: bash mac/pi-find.sh   - checks the Mac is on the iPhone hotspot and looks for the Pi.
MYIP=$(ipconfig getifaddr en0)
echo "Mac IP: ${MYIP:-none}"
# Some hotspots (e.g. on 5G) hand the Mac an odd address and only reach the Pi over
# IPv6 - so try the Pi's name first, whatever network we're on.
if ssh -i "${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}" -o ConnectTimeout=8 -o BatchMode=yes tim@mooderemote.local true 2>/dev/null; then
  echo "  FOUND mooderemote.local - ready, e.g. bash mac/status.sh"; exit 0
fi
case "$MYIP" in
  172.20.10.*) echo "  OK - Mac is on the iPhone hotspot" ;;
  *) echo "  Mac is NOT on the hotspot - join 'timiphone' in the Wi-Fi menu first"; exit 1 ;;
esac

echo "Looking up mooderemote.local..."
if ping -c1 -t3 mooderemote.local >/dev/null 2>&1; then
  echo "  FOUND by name - ready - e.g. bash mac/slides.sh"; exit 0
fi
echo "  not found by name - scanning the hotspot for SSH..."
for i in $(seq 2 14); do
  ip=172.20.10.$i
  [ "$ip" = "$MYIP" ] && continue
  if nc -z -G 1 $ip 22 2>/dev/null; then
    echo "  SSH answering at $ip - probably the Pi. Use:  PI=$ip bash mac/<script>.sh"
    FOUND=1
  fi
done
[ -n "$FOUND" ] || echo "  Pi not on the hotspot yet - keep the iPhone's Personal Hotspot screen open, wait a minute, run again."
