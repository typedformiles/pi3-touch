#!/bin/bash
# Usage: bash mac/deploy.sh        - install/update Pi3 Touch on the Pi (Mac on the same network).
# Packs the repo and installs it over a single SSH connection (kind to slow phone hotspots).
# Env: PI=<host or IP> (default mooderemote.local), PI_KEY=<ssh key>, SKIP_APT=1
REPO=$(cd "$(dirname "$0")/.." && pwd)
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}

echo "Deploying $(git -C "$REPO" describe --always --dirty 2>/dev/null) to $PI..."
# The install runs as its own job on the Pi, logging to ~/pi3-deploy.log, so a dropped
# connection can't kill it half way; this end just follows the log until it finishes.
COPYFILE_DISABLE=1 tar -C "$REPO" --no-xattrs --no-mac-metadata -czf - --exclude '*.png' --exclude '__pycache__' --exclude '.DS_Store' \
    common launcher apps pi |
  ssh -i "$KEY" -o ConnectTimeout=15 -o ServerAliveInterval=15 tim@"$PI" '
    LOG=$HOME/pi3-deploy.log
    rm -rf ~/pi3-touch "$LOG" && mkdir ~/pi3-touch && tar -xzf - -C ~/pi3-touch &&
    sudo systemd-run --quiet --collect --unit=pi3-deploy-$(date +%s) \
      --setenv=SKIP_APT='"$SKIP_APT"' --setenv=PI_USER=$USER \
      /bin/bash -c "/bin/bash $HOME/pi3-touch/pi/install.sh > $LOG 2>&1" || exit 1
    n=0
    for i in $(seq 1 900); do
      if [ -f "$LOG" ]; then
        tail -n +$((n + 1)) "$LOG"; n=$(wc -l < "$LOG")
        grep -q -e "INSTALL OK" -e "INSTALL FAILED" "$LOG" && break
      fi
      sleep 2
    done
    grep -q "INSTALL OK" "$LOG"' || { echo; echo "Lost the connection or the install failed - the install keeps going on the Pi."; echo "Check later with: bash mac/status.sh   (full log: ~/pi3-deploy.log on the Pi)"; exit 1; }
