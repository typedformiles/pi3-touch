#!/bin/bash
# Usage: bash mac/status.sh   - what's running on the Pi and recent logs.
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}
ssh -i "$KEY" -o ConnectTimeout=15 tim@"$PI" '
echo "== $(hostname), up $(uptime -p | sed "s/up //")"
echo "== screens"; for c in /sys/class/drm/card*-HDMI-A-1 /sys/class/drm/card*-DPI-1; do [ -e $c ] && echo "  $(basename $c): $(cat $c/status) $(head -1 $c/modes)"; done
echo "== last app: $(cat /var/lib/pi3-touch/last-app 2>/dev/null || echo none)"
for u in pi3-launcher pi3-booth-show pi3-booth-touch pi3-moode-remote; do printf "  %-18s %s\n" $u "$(systemctl is-active $u)"; done
echo "== recent"; journalctl -b --no-pager -o short -n 12 -u pi3-launcher -u pi3-booth-show -u pi3-booth-touch -u pi3-moode-remote -u pi3-home | grep -v "VT "
echo "== power"; vcgencmd get_throttled'
