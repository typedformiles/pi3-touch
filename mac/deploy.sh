#!/bin/bash
# Usage: bash mac/deploy.sh        - install/update Pi3 Touch on the Pi (Mac on the same network).
# Packs the repo and installs it over a single SSH connection (kind to slow phone hotspots).
# Env: PI=<host or IP> (default mooderemote.local), PI_KEY=<ssh key>, SKIP_APT=1
REPO=$(cd "$(dirname "$0")/.." && pwd)
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}

echo "Deploying $(git -C "$REPO" describe --always --dirty 2>/dev/null) to $PI..."
COPYFILE_DISABLE=1 tar -C "$REPO" -czf - --exclude '*.png' --exclude '__pycache__' --exclude '.DS_Store' \
    common launcher apps pi |
  ssh -i "$KEY" -o ConnectTimeout=15 tim@"$PI" \
    "rm -rf ~/pi3-touch && mkdir ~/pi3-touch && tar -xzf - -C ~/pi3-touch && sudo SKIP_APT=$SKIP_APT bash ~/pi3-touch/pi/install.sh"
