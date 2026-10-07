#!/bin/bash
# Usage: bash mac/demo-tour.sh [delay]   - hands-free tour of the apps on the Pi, for filming.
# Starts after `delay` seconds (default 10) and takes about 85 seconds. It runs as its own job
# on the Pi, so a dropped connection doesn't stop it; progress is in ~/demo-tour.log there.
# Env: PI=<host or IP> (default mooderemote.local), PI_KEY=<ssh key>
REPO=$(cd "$(dirname "$0")/.." && pwd)
PI=${PI:-mooderemote.local}
KEY=${PI_KEY:-$HOME/.ssh/mooderemote_ed25519}
DELAY=${1:-10}

ssh -i "$KEY" -o ConnectTimeout=15 tim@"$PI" "cat > ~/demo_tour.py" < "$REPO/mac/demo_tour.py" || exit 1
ssh -i "$KEY" -o ConnectTimeout=15 tim@"$PI" "
  systemd-run --user --quiet --collect --unit=pi3-demo-tour-\$(date +%s) \
    /bin/sh -c 'python3 ~/demo_tour.py $DELAY > ~/demo-tour.log 2>&1' 2>/dev/null ||
  { nohup python3 ~/demo_tour.py $DELAY > ~/demo-tour.log 2>&1 & }
  sleep 1; tail -f ~/demo-tour.log & T=\$!
  while ! grep -q 'tour done' ~/demo-tour.log 2>/dev/null; do sleep 1; done; sleep 1; kill \$T"
