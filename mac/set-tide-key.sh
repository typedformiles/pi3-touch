#!/bin/bash
# Usage: bash mac/set-tide-key.sh   - save your ADMIRALTY UK Tidal API key on the Pi (Weather's tides).
# Asks for the key here (hidden) and sends it straight to the Pi, into /etc/pi3-touch/admiralty-key
# (readable by root and you only), so it never appears on screen, in the repo or in shell history.
# Then checks the key works by looking up the tide stations. Get a key (free, "UK Tidal API -
# Discovery"): https://admiraltyapi.portal.azure-api.net
# Run it in your own Terminal (it needs to prompt), with the Mac on the Pi's current network.
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}

read -r -s -p "ADMIRALTY API key (typing is hidden): " TIDE_KEY; echo
TIDE_KEY=$(printf '%s' "$TIDE_KEY" | tr -d '[:space:]')
[ -n "$TIDE_KEY" ] || { echo "no key entered"; exit 1; }
printf '%s\n' "$TIDE_KEY" | ssh -i "$KEY" -o ConnectTimeout=15 tim@"$PI" '
  F=/etc/pi3-touch/admiralty-key
  sudo install -d -m 755 /etc/pi3-touch &&
  sudo sh -c "umask 027; cat > $F" && sudo chgrp "$(id -gn)" $F || exit 1
  echo "Saved the key on the Pi. Checking it with ADMIRALTY..."
  H=$(mktemp); chmod 600 "$H"; echo "Ocp-Apim-Subscription-Key: $(cat $F)" > "$H"
  for name in Hunstanton Herne%20Bay; do
    code=$(curl -s -o "$H.out" -w "%{http_code}" -H @"$H" \
      "https://admiraltyapi.azure-api.net/uktidalapi/api/V1/Stations?name=$name")
    printf "  %-12s HTTP %s  " "$(echo $name | sed "s/%20/ /")" "$code"
    python3 -c "import json,sys; print(\", \".join(f[\"properties\"][\"Name\"]+\" (\"+f[\"properties\"][\"Id\"]+\")\" for f in json.load(open(sys.argv[1])).get(\"features\",[])) or \"no station found\")" "$H.out" 2>/dev/null || echo
  done
  rm -f "$H" "$H.out"
  [ "$code" = 200 ] && echo "Key works." || echo "Key NOT accepted - check it is the Primary key of a UK Tidal API - Discovery subscription."
  sudo systemctl try-restart pi3-weather.service 2>/dev/null; true' ||
  { echo "Couldn't reach the Pi - is the Mac on the same network? (bash mac/pi-find.sh)"; exit 1; }
