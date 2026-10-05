#!/bin/bash
# Usage: bash mac/add-wifi.sh <SSID> [priority]
# Saves a Wi-Fi network on the Pi. Asks for the password here (hidden) and sends it straight
# to the Pi, so it never appears on screen or in the repo. Higher priority = preferred at
# boot (default 10; the network set in Raspberry Pi Imager is 0). Doesn't switch networks now.
# Run it in your own Terminal (it needs to prompt), with the Mac on the Pi's current network.
SSID=${1:?usage: bash mac/add-wifi.sh <SSID> [priority]}
PRIO=${2:-10}
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}
case "$SSID" in *"'"*) echo "SSIDs containing ' aren't supported"; exit 1;; esac

read -r -s -p "Wi-Fi password for $SSID: " PW; echo
[ -n "$PW" ] || { echo "no password entered"; exit 1; }
printf '%s' "$PW" | ssh -i "$KEY" -o ConnectTimeout=15 tim@"$PI" "
  PW=\$(cat)
  sudo nmcli connection delete id '$SSID' >/dev/null 2>&1
  sudo nmcli connection add type wifi ifname wlan0 con-name '$SSID' ssid '$SSID' \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk \"\$PW\" \
    connection.autoconnect yes connection.autoconnect-priority $PRIO >/dev/null &&
  echo 'Saved $SSID on the Pi (priority $PRIO). Saved networks:' &&
  nmcli -t -f NAME,AUTOCONNECT-PRIORITY connection show | grep -v '^lo:' | sed 's/^/  /'" ||
  { echo "Couldn't save it - is the Mac on the same network as the Pi? (bash mac/pi-find.sh)"; exit 1; }
