#!/bin/bash
# Usage: bash mac/set-pihole-key.sh   - save your Pi-hole's app password on the Pi (the Pi-hole app).
# Asks for it here (hidden) and sends it straight to the Pi, into /etc/pi3-touch/pihole-password
# (readable by root and you only), so it never appears on screen, in the repo or in shell history.
# Then checks it by logging in to the Pi-hole. Make one in the Pi-hole's web page: Settings >
# Web interface / API > Configure app password (copy it before closing the box, then Enable).
# Run it in your own Terminal (it needs to prompt), with the Mac on the Pi's current network.
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}

read -r -s -p "Pi-hole app password (typing is hidden): " PH_PASS; echo
PH_PASS=$(printf '%s' "$PH_PASS" | tr -d '[:space:]')
[ -n "$PH_PASS" ] || { echo "no password entered"; exit 1; }
printf '%s\n' "$PH_PASS" | ssh -i "$KEY" -o ConnectTimeout=15 tim@"$PI" '
  F=/etc/pi3-touch/pihole-password
  sudo install -d -m 755 /etc/pi3-touch &&
  sudo sh -c "umask 027; cat > $F" && sudo chgrp "$(id -gn)" $F || exit 1
  echo "Saved the password on the Pi. Checking it with the Pi-hole..."
  if [ -f /opt/pi3-touch/apps/pihole/pihole_data.py ]; then
    python3 /opt/pi3-touch/apps/pihole/pihole_data.py --check
  else
    echo "The Pi-hole app is not installed yet - run bash mac/deploy.sh, then this again to check."
  fi
  sudo systemctl try-restart pi3-pihole.service 2>/dev/null; true' ||
  { echo "Couldn't reach the Pi - is the Mac on the same network? (bash mac/pi-find.sh)"; exit 1; }
